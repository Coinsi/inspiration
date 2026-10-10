"""Actual multipart parsing and isolated subprocess supervision boundaries."""
# ruff: noqa: F811 -- imported pytest fixtures are referenced by parameter name

import importlib
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from test_bridge import bridge, request  # noqa: F401
from test_recovery import recovery, wrappers  # noqa: F401


@pytest.fixture
def capacity(bridge):
    return importlib.import_module("mediacms_bridge.capacity")


def test_reserve_is_checked_on_both_filesystems(capacity, monkeypatch):
    usage = Mock(side_effect=[SimpleNamespace(free=20), SimpleNamespace(free=9)])
    monkeypatch.setattr(capacity.shutil, "disk_usage", usage)
    with pytest.raises(capacity.DiskCapacityError, match="Insufficient"):
        capacity.require_space(
            SimpleNamespace(INSPIRATION_DISK_RESERVE_BYTES=5),
            ("temporary", "media"),
            additional_bytes=5,
        )
    assert [call.args[0] for call in usage.call_args_list] == ["temporary", "media"]


def test_unknown_storage_state_rejects_work(capacity, monkeypatch):
    monkeypatch.setattr(
        capacity.shutil, "disk_usage", Mock(side_effect=OSError("private path"))
    )
    with pytest.raises(capacity.DiskCapacityError, match="cannot be verified") as error:
        capacity.require_space(settings, ("media",))
    assert "private path" not in str(error.value)


def handler(bridge):
    module = importlib.import_module("mediacms_bridge.uploadhandler")
    upload = module.CapacityUploadHandler()
    upload.new_file("media_file", "source.mp4", "video/mp4", None, None)
    return module, upload, Path(upload.file.temporary_file_path())


def test_exact_byte_limit_rejects_next_chunk_and_removes_own_partial(
    bridge, monkeypatch
):
    monkeypatch.setattr(settings, "INSPIRATION_MAX_UPLOAD_BYTES", 5, raising=False)
    module, upload, path = handler(bridge)
    upload.receive_data_chunk(b"12345", 0)
    upload.file.flush()
    assert path.read_bytes() == b"12345"
    with pytest.raises(module.UploadTooLarge):
        upload.receive_data_chunk(b"6", 5)
    assert not path.exists()


def test_midstream_disk_pressure_stops_before_next_write(capacity, bridge, monkeypatch):
    module, upload, path = handler(bridge)
    upload.receive_data_chunk(b"abc", 0)
    monkeypatch.setattr(
        capacity.shutil, "disk_usage", lambda _: SimpleNamespace(free=2)
    )
    with pytest.raises(module.StorageUnavailable):
        upload.receive_data_chunk(b"def", 3)
    assert upload.received_bytes == 3 and not path.exists()


@pytest.mark.parametrize("failure", ["capacity", "size"])
def test_real_drf_multipart_rejects_before_database(
    bridge, capacity, monkeypatch, failure
):
    view, _, cursor = bridge
    if failure == "capacity":
        monkeypatch.setattr(
            settings, "INSPIRATION_DISK_RESERVE_BYTES", 10, raising=False
        )
        monkeypatch.setattr(
            capacity.shutil, "disk_usage", lambda _: SimpleNamespace(free=1)
        )
    else:
        monkeypatch.setattr(settings, "INSPIRATION_MAX_UPLOAD_BYTES", 4, raising=False)
    req = request(
        "post",
        "/",
        {
            "upload_key": "run123",
            "source_sha256": "0" * 64,
            "media_file": SimpleUploadedFile("source.mp4", b"video"),
        },
        format="multipart",
    )
    response = view.UploadView.as_view()(req)
    assert response.status_code == (507 if failure == "capacity" else 413)
    cursor.execute.assert_not_called()
    assert not list(Path(settings.FILE_UPLOAD_TEMP_DIR).glob("*.upload*"))
    req.close()


def test_final_copy_has_additional_source_size_admission(bridge, capacity, monkeypatch):
    import hashlib

    view, _, _ = bridge
    view.Media.objects.filter.return_value.first.return_value = None
    check = Mock(
        side_effect=capacity.DiskCapacityError("Insufficient media storage space")
    )
    monkeypatch.setattr(view, "require_space", check)
    req = request(
        "post",
        "/",
        {
            "upload_key": "run123",
            "source_sha256": hashlib.sha256(b"video").hexdigest(),
            "media_file": SimpleUploadedFile("source.mp4", b"video"),
        },
        format="multipart",
    )
    response = view.UploadView.as_view()(req)
    assert response.status_code == 507
    assert check.call_args.kwargs == {"additional_bytes": 5}
    req.close()


def test_processing_preflight_does_not_spawn_when_storage_is_full(
    wrappers, monkeypatch
):
    _, tasks = wrappers
    monkeypatch.setattr(
        tasks,
        "check_processing_space",
        Mock(side_effect=tasks.DiskCapacityError("Insufficient space")),
    )
    spawn = Mock()
    monkeypatch.setattr(tasks.subprocess, "Popen", spawn)
    with pytest.raises(tasks.DiskCapacityError):
        tasks._run("encode", 1)
    spawn.assert_not_called()
    assert (
        tasks.Encoding.objects.filter.return_value.update.call_args.kwargs["logs"]
        == "Insufficient space"
    )


def test_low_disk_duplicate_cannot_fail_live_lease_holder(wrappers, monkeypatch):
    _, tasks = wrappers
    monkeypatch.setattr(
        tasks,
        "check_processing_space",
        Mock(side_effect=tasks.DiskCapacityError("Insufficient space")),
    )
    monkeypatch.setattr(tasks, "available_lock", lambda *_: nullcontext(False))
    with pytest.raises(tasks.DiskCapacityError):
        tasks._run("encode", 1)
    tasks.Encoding.objects.filter.assert_not_called()


def test_runtime_pressure_kills_only_own_group_and_records_retryable_failure(
    wrappers, monkeypatch
):
    _, tasks = wrappers
    capacity = Mock(
        side_effect=[None, None, tasks.DiskCapacityError("Insufficient space; retry")]
    )
    monkeypatch.setattr(tasks, "check_processing_space", capacity)
    child = SimpleNamespace(
        pid=987,
        wait=Mock(
            side_effect=[tasks.subprocess.TimeoutExpired("private runner", 1), 0]
        ),
        poll=lambda: None,
    )
    spawn = Mock(return_value=child)
    monkeypatch.setattr(tasks.subprocess, "Popen", spawn)
    kill = Mock()
    monkeypatch.setattr(tasks.os, "killpg", kill, raising=False)
    monkeypatch.setattr(tasks.signal, "SIGKILL", 9, raising=False)
    with pytest.raises(tasks.DiskCapacityError):
        tasks._run("encode", 7)
    assert spawn.call_args.kwargs["start_new_session"] is True
    assert child.wait.call_args_list[0].kwargs == {"timeout": 1}
    kill.assert_called_once_with(987, 9)
    assert (
        "retry"
        in tasks.Encoding.objects.filter.return_value.update.call_args.kwargs["logs"]
    )


def test_enough_space_keeps_normal_success_untouched(wrappers, monkeypatch):
    _, tasks = wrappers
    child = SimpleNamespace(pid=987, wait=Mock(return_value=0), poll=lambda: 0)
    monkeypatch.setattr(tasks.subprocess, "Popen", lambda *args, **kwargs: child)
    tasks._run("encode", 7)
    tasks.Encoding.objects.filter.assert_not_called()
    child.wait.assert_called_once_with(timeout=1)
