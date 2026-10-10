"""Scratch cleanup stays within one proven-stopped processing attempt."""

import importlib
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_bridge import bridge  # noqa: F401


@pytest.fixture
def scratch(bridge, monkeypatch):  # noqa: F811
    module = importlib.import_module("mediacms_bridge.scratch")
    owner = {"boot": "boot", "namespace": "pid:[123]", "pid": 456, "group": 456, "start": "789"}
    monkeypatch.setattr(module, "identity", lambda: dict(owner))
    monkeypatch.setattr(module, "stop_children", Mock())
    monkeypatch.setattr(module, "process_info", lambda _: None)
    monkeypatch.setattr(module, "group_members", lambda _: [])
    return module, owner


def old_attempt(root, owner, name="a" * 32):
    directory = root / "inspiration-jobs" / "encode-7" / name
    directory.mkdir(parents=True)
    (directory / "owner.json").write_text(json.dumps(owner))
    (directory / "incomplete.mp4").write_bytes(b"partial")
    return directory


def test_private_scratch_restores_settings_and_cleans_only_its_own_job(scratch, tmp_path):
    module, owner = scratch
    settings = SimpleNamespace(TEMP_DIRECTORY=str(tmp_path))
    original_tempdir = tempfile.tempdir
    original_environment = {name: os.environ.get(name) for name in ("TMPDIR", "TMP", "TEMP")}
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unchanged original")
    stale = old_attempt(tmp_path, owner)
    other_job = tmp_path / "inspiration-jobs" / "encode-8"
    other_job.mkdir()
    with module.job_scratch(settings, "encode", 7) as attempt:
        assert not stale.exists()
        assert tempfile.gettempdir() == settings.TEMP_DIRECTORY == str(attempt)
        assert all(os.environ[name] == str(attempt) for name in original_environment)
        (attempt / "partial.mp4").write_bytes(b"temporary")
    module.stop_children.assert_called_once()
    assert not attempt.exists()
    assert not attempt.parent.exists()
    assert other_job.is_dir() and source.read_bytes() == b"unchanged original"
    assert settings.TEMP_DIRECTORY == str(tmp_path) and tempfile.tempdir == original_tempdir
    assert {name: os.environ.get(name) for name in original_environment} == original_environment


def test_unknown_container_or_live_group_leftovers_are_retained(scratch, tmp_path, monkeypatch):
    module, owner = scratch
    foreign = old_attempt(tmp_path, {**owner, "namespace": "pid:[another]"})
    live = old_attempt(tmp_path, owner, "b" * 32)
    monkeypatch.setattr(module, "group_members", lambda _: [owner])
    with module.job_scratch(SimpleNamespace(TEMP_DIRECTORY=str(tmp_path)), "encode", 7):
        assert foreign.exists() and live.exists()
    assert foreign.exists() and live.exists()


def test_processing_error_still_stops_children_before_removing_files(
    scratch, tmp_path, monkeypatch
):
    module, _ = scratch
    seen = []

    def stopped():
        seen.append(attempt.exists())

    monkeypatch.setattr(module, "stop_children", stopped)
    with pytest.raises(RuntimeError, match="upstream failed"):
        with module.job_scratch(
            SimpleNamespace(TEMP_DIRECTORY=str(tmp_path)), "speech", 1
        ) as attempt:
            raise RuntimeError("upstream failed")
    assert seen == [True] and not attempt.exists()


def test_failed_quiescence_does_not_delete_possible_live_scratch(scratch, tmp_path, monkeypatch):
    module, _ = scratch
    settings = SimpleNamespace(TEMP_DIRECTORY=str(tmp_path))
    old_tempdir = tempfile.tempdir
    old_environment = {name: os.environ.get(name) for name in ("TMPDIR", "TMP", "TEMP")}
    monkeypatch.setattr(module, "stop_children", Mock(side_effect=RuntimeError("still alive")))
    try:
        with pytest.raises(RuntimeError, match="still alive"):
            with module.job_scratch(settings, "encode", 7) as attempt:
                pass
        assert attempt.exists()
    finally:
        # The production runner exits on this failure, whereas pytest shares a process.
        tempfile.tempdir = old_tempdir
        for key, value in old_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_scratch_refuses_path_escape(scratch, tmp_path):
    module, _ = scratch
    outside = tmp_path / "not-a-job"
    outside.mkdir()
    with pytest.raises(RuntimeError, match="Untrusted"):
        module.remove_attempt(outside, tmp_path / "inspiration-jobs")
    assert outside.is_dir()


def test_quiescence_signals_only_members_of_the_current_group(scratch, monkeypatch):
    module, _ = scratch
    # Restore the real implementation rather than the fixture's ordinary no-op.
    module = importlib.reload(module)
    monkeypatch.setattr(module.os, "getpid", lambda: 100)
    monkeypatch.setattr(module.os, "getpgrp", lambda: 100, raising=False)
    own = {"pid": 100, "group": 100, "state": "R", "start": "1"}
    child = {"pid": 101, "group": 100, "state": "S", "start": "2"}
    groups = iter([[own, child], [own]])
    monkeypatch.setattr(module, "group_members", lambda group: next(groups))
    monkeypatch.setattr(module, "process_info", lambda pid: child if pid == 101 else own)
    monkeypatch.setattr(module.signal, "SIGKILL", 9, raising=False)
    kill = Mock()
    monkeypatch.setattr(module.os, "kill", kill)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    module.stop_children()
    kill.assert_called_once_with(101, 9)
