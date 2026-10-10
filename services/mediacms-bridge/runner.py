"""Linux-only private runner; owns exactly one upstream job's process group."""

import ctypes
import logging
import os
import signal
import sys
import threading


def terminate_group(*_args):
    os.killpg(os.getpgrp(), signal.SIGKILL)


def arm_parent_guard(expected_parent):
    if sys.platform != "linux" or os.getpid() != os.getpgrp():
        raise RuntimeError("A dedicated Linux processing session is required")
    signal.signal(signal.SIGTERM, terminate_group)
    signal.signal(signal.SIGINT, terminate_group)
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Could not arm processing parent guard")
    if os.getppid() != expected_parent:
        terminate_group()


def run(kind, identifier):
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cms.settings")
    django.setup()
    from django.conf import settings
    from django.db import connection
    from files import tasks as upstream
    from files.models import Encoding, Media
    from files.models.subtitle import TranscriptionRequest

    from .recovery import TASK_PREFIX, lock_key, speech_delivery
    from .scratch import job_scratch

    # A dedicated raw connection keeps the lease alive while the upstream code
    # performs arbitrary Django connection management. Its heartbeat terminates
    # this job's process group if the exclusive lease cannot be maintained.
    params = connection.get_connection_params()
    params.update(
        connect_timeout=5,
        keepalives=1,
        keepalives_idle=5,
        keepalives_interval=1,
        keepalives_count=3,
        tcp_user_timeout=5000,
    )
    lease = connection.Database.connect(**params)
    lease.autocommit = True
    with lease.cursor() as cursor:
        cursor.execute("SET statement_timeout TO 3000")
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_key(kind, identifier)])
        if not cursor.fetchone()[0]:
            lease.close()
            return 75  # another delivery is already running; do not change its row
    finished = threading.Event()

    def heartbeat():
        while not finished.wait(1):
            try:
                with lease.cursor() as cursor:
                    cursor.execute("SELECT 1")
            except Exception:
                terminate_group()

    monitor = threading.Thread(target=heartbeat, daemon=True)
    monitor.start()
    try:
        with job_scratch(settings, kind, identifier):
            if kind == "initialize":
                from .views import _pipeline_snapshot, initialize_media

                media = Media.objects.filter(pk=identifier).first()
                if media is None:
                    return 0
                snapshot = _pipeline_snapshot(media)
                if not snapshot or snapshot["dispatch"] != "initializing":
                    return 0
                if media.state != "private" or not media.description.startswith("inspiration:"):
                    raise RuntimeError("Only private integration media can be initialized")
                initialize_media(media)
                return 0
            model = Encoding if kind == "encode" else TranscriptionRequest
            row = model.objects.select_related("media").filter(pk=identifier).first()
            if row is None or row.status in ("success", "fail"):
                return 0
            if row.media.state != "private" or not row.media.description.startswith("inspiration:"):
                raise RuntimeError("Only private integration media can run in this worker")
            if kind == "encode":
                if not row.task_id.startswith(TASK_PREFIX):
                    raise RuntimeError("Encoding does not belong to this integration")
                upstream.encode_media.apply(
                    args=[row.media.friendly_token, row.profile_id, row.pk],
                    kwargs={"force": True, "chunk": False},
                    task_id=row.task_id,
                    throw=True,
                )
            else:
                delivery = speech_delivery(row)
                if delivery is None:
                    raise RuntimeError("Transcription does not belong to this integration")
                # The native task only consumes pending requests. The exclusive lease
                # and dead-parent guard make resetting this same attempt safe.
                model.objects.filter(pk=row.pk).update(status="pending")
                upstream.whisper_transcribe.apply(
                    args=[row.media.friendly_token, row.translate_to_english],
                    task_id=delivery[0],
                    throw=True,
                )
            row.refresh_from_db()
            if row.status not in ("success", "fail"):
                model.objects.filter(pk=row.pk).update(
                    status="fail", logs="Upstream returned without a terminal state"
                )
            return 0
    finally:
        finished.set()
        monitor.join(timeout=2)
        lease.close()


def main():
    kind, identifier, parent = sys.argv[1:]
    if (
        kind not in ("initialize", "encode", "speech")
        or not identifier.isdecimal()
        or not parent.isdecimal()
    ):
        raise ValueError("Invalid private runner arguments")
    arm_parent_guard(int(parent))
    try:
        return run(kind, int(identifier))
    except BaseException:
        # Uncaught exceptions cannot leave an upstream child tree running.
        logging.exception("Isolated MediaCMS runner failed")
        terminate_group()


if __name__ == "__main__":
    raise SystemExit(main())
