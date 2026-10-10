"""Crash-recoverable wrappers around real MediaCMS encoding and Whisper tasks."""

import os
import signal
import subprocess
import sys
import uuid

from celery import shared_task
from django.conf import settings
from django.db import transaction
from files.models import Encoding, Media
from files.models.subtitle import TranscriptionRequest

from .capacity import DiskCapacityError, check_processing_space
from .recovery import TASK_PREFIX, available_lock, speech_delivery


def enqueue_initialization(media):
    from .views import _pipeline_snapshot

    snapshot = _pipeline_snapshot(media)
    initialize.apply_async(args=[media.pk], task_id=snapshot["task_id"])


def enqueue_encodings(media, profiles):
    # All expected rows commit before any message can be received. A publisher
    # crash leaves durable task IDs which authenticated manifest polling repairs.
    with transaction.atomic():
        rows = [
            Encoding.objects.create(
                media=media,
                profile=profile,
                task_id=TASK_PREFIX + uuid.uuid4().hex,
            )
            for profile in profiles
        ]
    for row in rows:
        encode.apply_async(args=[row.pk], task_id=row.task_id)


def enqueue_transcription(row):
    delivery = speech_delivery(row)
    if delivery is None:
        raise ValueError("Missing transcription delivery identity")
    transcribe.apply_async(args=[row.pk], task_id=delivery[0])


def _run(kind, identifier):
    # The runner owns a dedicated session/process group and arms PDEATHSIG before
    # doing work. A hard-killed Celery process therefore cannot orphan FFmpeg or
    # Whisper, and cleanup never kills by broad process name or source path.
    child = None
    failed = False
    failure_message = "Processing subprocess failed; retry this stage from inspiration"
    try:
        check_processing_space(settings)
        child = subprocess.Popen(
            [sys.executable, "-m", "mediacms_bridge.runner", kind, str(identifier), str(os.getpid())],
            start_new_session=True,
        )
        while True:
            check_processing_space(settings)
            try:
                result = child.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                continue
        if result not in (0, 75):
            failed = True
            raise RuntimeError("MediaCMS processing subprocess failed")
    except DiskCapacityError as exc:
        failed = True
        failure_message = str(exc)
        raise
    except BaseException:
        failed = True
        raise
    finally:
        if child is not None and child.poll() is None:
            # Only the process group created for this particular invocation.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
        if failed:
            # A duplicate delivery must not fail another live lease holder.
            with available_lock(kind, identifier) as free:
                if free:
                    _mark_failed(kind, identifier, failure_message)


def _mark_failed(kind, identifier, failure_message):
    if kind == "initialize":
        from .views import _mark_pipeline_failed

        media = Media.objects.filter(pk=identifier).first()
        if media:
            _mark_pipeline_failed(media, failure_message)
    else:
        model = Encoding if kind == "encode" else TranscriptionRequest
        model.objects.filter(pk=identifier, status__in=("pending", "running")).update(
            status="fail",
            logs=failure_message,
        )


@shared_task(
    name="inspiration_initialize",
    queue="long_tasks",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=7200,
    time_limit=7230,
)
def initialize(self, identifier):
    _run("initialize", identifier)


@shared_task(
    name="inspiration_encode",
    queue="long_tasks",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=7200,
    time_limit=7230,
)
def encode(self, identifier):
    _run("encode", identifier)


@shared_task(
    name="inspiration_transcribe",
    queue="long_tasks",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=7200,
    time_limit=7230,
)
def transcribe(self, identifier):
    _run("speech", identifier)
