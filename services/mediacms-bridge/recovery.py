"""Durable delivery identities and per-job PostgreSQL locks for the sidecar.

An absent queue result is not evidence of failure. A job is redelivered only
when its persisted state is unfinished, its last delivery is old, and its
runner's exclusive lock is free. Duplicate deliveries share the same identity
and the runner checks the terminal database row under that lock.
"""

import hashlib
import time
import uuid
from contextlib import contextmanager

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from files.models import Encoding, Media
from files.models.subtitle import TranscriptionRequest

TASK_PREFIX = "inspiration-"
SPEECH_PREFIX = "inspiration-task:"


def lock_key(kind, identifier):
    value = int.from_bytes(
        hashlib.sha256(f"inspiration:{kind}:{identifier}".encode()).digest()[:8], "big"
    )
    return value if value < 2**63 else value - 2**64


@contextmanager
def available_lock(kind, identifier):
    key = lock_key(kind, identifier)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = bool(cursor.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def speech_delivery(row):
    fields = (row.logs or "").split(":")
    if len(fields) != 3 or fields[0] != SPEECH_PREFIX[:-1] or not fields[1].startswith(TASK_PREFIX):
        return None
    try:
        return fields[1], float(fields[2])
    except ValueError:
        return None


def new_speech_delivery():
    return f"{SPEECH_PREFIX}{TASK_PREFIX}{uuid.uuid4().hex}:{time.time()}"


def recover_media(media):
    """Reconcile only this authenticated media's previously dispatched jobs."""
    import json

    from .tasks import encode, enqueue_initialization, transcribe
    from .views import PIPELINE_MARKER, _pipeline_snapshot

    delay = max(5, getattr(settings, "INSPIRATION_RECOVERY_DELAY_SECONDS", 30))
    before = timezone.now().timestamp() - delay
    snapshot = _pipeline_snapshot(media)
    if snapshot and snapshot.get("updated", time.time()) < before:
        kind = "initialize" if snapshot["dispatch"] == "initializing" else "dispatch"
        if snapshot["dispatch"] in ("initializing", "submitting"):
            redeliver = None
            with available_lock(kind, media.pk) as free:
                if free:
                    with transaction.atomic():
                        current = Media.objects.select_for_update().get(pk=media.pk)
                        latest = _pipeline_snapshot(current)
                        if (
                            latest
                            and latest["dispatch"] == snapshot["dispatch"]
                            and latest.get("updated", time.time()) < before
                        ):
                            latest["updated"] = time.time()
                            if latest["dispatch"] == "initializing":
                                redeliver = current
                            else:
                                # No live producer remains; queued rows below can
                                # finish, but an incomplete batch must be retried.
                                latest["dispatch"] = "failed"
                            current.description = (
                                current.description.partition(PIPELINE_MARKER)[0]
                                + PIPELINE_MARKER
                                + json.dumps(latest, separators=(",", ":"))
                            )
                            Media.objects.filter(pk=current.pk).update(
                                description=current.description
                            )
            if redeliver is not None:
                enqueue_initialization(redeliver)
    pending = list(media.encodings.filter(status__in=("pending", "running")))
    for candidate in pending:
        if (
            not candidate.task_id.startswith(TASK_PREFIX)
            or candidate.update_date.timestamp() > before
        ):
            continue
        delivery = None
        with available_lock("encode", candidate.pk) as free:
            if not free:
                continue
            with transaction.atomic():
                row = Encoding.objects.select_for_update().get(pk=candidate.pk)
                if row.status not in ("pending", "running") or row.update_date.timestamp() > before:
                    continue
                Encoding.objects.filter(pk=row.pk).update(update_date=timezone.now())
                delivery = (row.pk, row.task_id)
        if delivery:
            encode.apply_async(args=[delivery[0]], task_id=delivery[1])
    for candidate in media.transcriptionrequests.filter(status__in=("pending", "running")):
        previous = speech_delivery(candidate)
        if previous is None or previous[1] > before:
            continue
        delivery = None
        with available_lock("speech", candidate.pk) as free:
            if not free:
                continue
            with transaction.atomic():
                row = TranscriptionRequest.objects.select_for_update().get(pk=candidate.pk)
                previous = speech_delivery(row)
                if (
                    row.status not in ("pending", "running")
                    or previous is None
                    or previous[1] > before
                ):
                    continue
                row.logs = f"{SPEECH_PREFIX}{previous[0]}:{time.time()}"
                row.save(update_fields=["logs"])
                delivery = (row.pk, previous[0])
        if delivery:
            transcribe.apply_async(args=[delivery[0]], task_id=delivery[1])
