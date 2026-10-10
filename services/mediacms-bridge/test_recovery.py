"""Recovery state contracts; actual child death is covered by deployed smoke."""

import importlib
import sys
import time
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.utils import timezone
from test_bridge import bridge, encoding_media  # noqa: F401


@pytest.fixture
def recovery(bridge, monkeypatch):  # noqa: F811
    view, tasks, _ = bridge
    sys.modules["files.models"].Encoding = SimpleNamespace(objects=Mock())
    tasks.encode = SimpleNamespace(apply_async=Mock())
    tasks.transcribe = SimpleNamespace(apply_async=Mock())
    monkeypatch.delitem(sys.modules, "mediacms_bridge.recovery")
    module = importlib.import_module("mediacms_bridge.recovery")
    monkeypatch.setattr(module, "available_lock", lambda *_: nullcontext(True))
    return module, view, tasks


def pending_media(view, *, encodings=(), speeches=(), dispatch="submitted"):
    media = encoding_media(view, [], dispatch=dispatch, profiles=(1,))
    media.encodings = SimpleNamespace(filter=lambda **_: encodings)
    media.transcriptionrequests = SimpleNamespace(filter=lambda **_: speeches)
    return media


def test_live_encoding_is_not_redelivered(recovery, monkeypatch):
    module, view, tasks = recovery
    row = SimpleNamespace(
        pk=1,
        task_id="inspiration-same-id",
        status="running",
        update_date=timezone.now() - timedelta(minutes=2),
    )
    media = pending_media(view, encodings=[row])
    monkeypatch.setattr(module, "available_lock", lambda *_: nullcontext(False))
    module.recover_media(media)
    tasks.encode.apply_async.assert_not_called()


def test_lost_worker_job_is_redelivered_once_with_same_identity(recovery):
    module, view, tasks = recovery
    row = SimpleNamespace(
        pk=1,
        task_id="inspiration-same-id",
        status="running",
        update_date=timezone.now() - timedelta(minutes=2),
    )
    module.Encoding.objects.select_for_update.return_value.get.return_value = row
    module.Encoding.objects.filter.return_value.update.side_effect = lambda **kwargs: setattr(
        row, "update_date", kwargs["update_date"]
    )
    media = pending_media(view, encodings=[row])
    module.recover_media(media)
    module.recover_media(media)
    tasks.encode.apply_async.assert_called_once_with(args=[1], task_id="inspiration-same-id")
    assert row.status == "running"  # the exclusive runner, not a poll, resets native work


def test_lost_transcription_keeps_identity_and_does_not_duplicate_delivery(recovery):
    module, view, tasks = recovery
    row = SimpleNamespace(
        pk=2,
        status="running",
        logs=f"inspiration-task:inspiration-speech:{time.time() - 120}",
        save=Mock(),
    )
    module.TranscriptionRequest.objects.select_for_update.return_value.get.return_value = row
    media = pending_media(view, speeches=[row])
    module.recover_media(media)
    module.recover_media(media)
    tasks.transcribe.apply_async.assert_called_once_with(args=[2], task_id="inspiration-speech")
    assert row.status == "running"


@pytest.mark.parametrize("dispatch", ["initializing", "submitting"])
def test_post_commit_dispatch_crash_is_reconciled(recovery, dispatch):
    module, view, tasks = recovery
    media = pending_media(view, dispatch=dispatch)
    snapshot = view._pipeline_snapshot(media)
    snapshot.update(updated=time.time() - 120, task_id="inspiration-initialize")
    import json

    media.description = (
        media.description.partition(view.PIPELINE_MARKER)[0]
        + view.PIPELINE_MARKER
        + json.dumps(snapshot)
    )
    module.Media.objects.select_for_update.return_value.get.return_value = media
    module.recover_media(media)
    if dispatch == "initializing":
        tasks.enqueue_initialization.assert_called_once_with(media)
        assert view._pipeline_snapshot(media)["task_id"] == "inspiration-initialize"
    else:
        assert view._pipeline_snapshot(media)["dispatch"] == "failed"
        assert view._pipeline_snapshot(media)["profiles"] == [1]


@pytest.fixture
def wrappers(recovery, monkeypatch):
    module, _, _ = recovery
    monkeypatch.delitem(sys.modules, "mediacms_bridge.tasks")
    tasks = importlib.import_module("mediacms_bridge.tasks")
    return module, tasks


def test_all_encoding_rows_commit_before_any_message_is_published(wrappers, monkeypatch):
    _, tasks = wrappers
    inside = False
    rows = []

    @contextmanager
    def atomic():
        nonlocal inside
        inside = True
        yield
        inside = False

    monkeypatch.setattr(tasks.transaction, "atomic", atomic)

    def create(**kwargs):
        assert inside
        row = SimpleNamespace(pk=len(rows) + 1, **kwargs)
        rows.append(row)
        return row

    tasks.Encoding.objects.create.side_effect = create
    calls = []

    def publish(**kwargs):
        assert not inside and len(rows) == 3
        calls.append(kwargs)

    monkeypatch.setattr(tasks.encode, "apply_async", publish)
    tasks.enqueue_encodings(object(), [object(), object(), object()])
    assert len(calls) == 3
    assert [item["task_id"] for item in calls] == [row.task_id for row in rows]


def test_duplicate_runner_does_not_mark_live_job_failed(wrappers, monkeypatch):
    _, tasks = wrappers
    child = SimpleNamespace(pid=987, wait=lambda **_: 75, poll=lambda: 75)
    monkeypatch.setattr(tasks.subprocess, "Popen", lambda *args, **kwargs: child)
    tasks._run("encode", 1)
    tasks.Encoding.objects.filter.assert_not_called()


def test_wrapper_exception_terminates_only_its_own_process_group(wrappers, monkeypatch):
    _, tasks = wrappers
    child = SimpleNamespace(
        pid=987, wait=Mock(side_effect=[RuntimeError("timeout"), 0]), poll=lambda: None
    )
    monkeypatch.setattr(tasks.subprocess, "Popen", lambda *args, **kwargs: child)
    kill = Mock()
    monkeypatch.setattr(tasks.os, "killpg", kill, raising=False)
    monkeypatch.setattr(tasks.signal, "SIGKILL", 9, raising=False)
    with pytest.raises(RuntimeError, match="timeout"):
        tasks._run("encode", 1)
    kill.assert_called_once_with(987, tasks.signal.SIGKILL)
    tasks.Encoding.objects.filter.return_value.update.assert_called_once()
