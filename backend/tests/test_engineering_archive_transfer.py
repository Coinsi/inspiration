"""Resume bytes, revalidate local caches, and preserve the fixed export identity."""

import hashlib
import io
import os
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.core.errors import Conflict
from app.modules.engineering import archive_transfer as transfer
from app.modules.generation.media_engine import Canceled


def test_staging_has_exclusive_ownership_and_releases_after_failure(tmp_path):
    with pytest.raises(RuntimeError, match="interrupted"):
        with transfer.ownership(tmp_path):
            with pytest.raises(Conflict, match="执行进程"):
                with transfer.ownership(tmp_path):
                    pytest.fail("duplicate worker entered staging")
            raise RuntimeError("interrupted")
    with transfer.ownership(tmp_path):
        assert (tmp_path / "worker.lock").exists()


def test_process_exit_releases_staging_lock(tmp_path):
    script = (
        "import sys; from app.modules.engineering.archive_transfer import ownership; "
        "guard=ownership(sys.argv[1]); guard.__enter__(); "
        "print('owned', flush=True); sys.stdin.read(); __import__('os')._exit(9)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        assert child.stdout.readline().strip() == "owned"
        with pytest.raises(Conflict):
            with transfer.ownership(tmp_path):
                pytest.fail("another process holds the checkpoint")
        child.communicate(input="crash", timeout=10)
        assert child.returncode == 9
        with transfer.ownership(tmp_path):
            pass
    finally:
        if child.poll() is None:
            child.communicate(input="crash", timeout=10)


def test_resume_after_interruption_reads_only_remaining_bytes(tmp_path, monkeypatch):
    data = bytes(range(256)) * 100
    entry = {"hash": hashlib.sha256(data).hexdigest(), "size": len(data)}
    directory = transfer.prepare(tmp_path, {"fixed_revision": 3})
    ranges = []

    @contextmanager
    def source(blob, start, length):
        ranges.append((start, length))
        yield io.BytesIO(data[start : start + length])

    monkeypatch.setattr(transfer.cas, "open_range", source)
    canceled = False

    def stop_after_chunk(count):
        nonlocal canceled
        if count >= 4096:
            canceled = True

    with pytest.raises(Canceled):
        transfer.stage(
            directory,
            None,
            entry,
            canceled=lambda: canceled,
            progress=stop_after_chunk,
            chunk_size=4096,
        )
    assert (directory / (entry["hash"] + ".partial")).stat().st_size == 4096
    final = transfer.stage(
        directory, None, entry, canceled=lambda: False, progress=lambda _: None, chunk_size=4096
    )
    assert final.read_bytes() == data
    assert ranges == [(0, len(data)), (4096, len(data) - 4096)]
    transfer.stage(
        directory, None, entry, canceled=lambda: False, progress=lambda _: None, chunk_size=4096
    )
    assert len(ranges) == 2  # A verified completed source does not need network reads.


def test_corrupt_partial_is_never_promoted_and_revision_cannot_change(tmp_path, monkeypatch):
    data = b"fixed bytes"
    entry = {"hash": hashlib.sha256(data).hexdigest(), "size": len(data)}
    directory = transfer.prepare(tmp_path, {"fixed_revision": 3})
    with pytest.raises(Conflict):
        transfer.prepare(tmp_path, {"fixed_revision": 4})
    partial = directory / (entry["hash"] + ".partial")
    partial.write_bytes(b"wrong")

    @contextmanager
    def source(blob, start, length):
        yield io.BytesIO(data[start : start + length])

    monkeypatch.setattr(transfer.cas, "open_range", source)
    with pytest.raises(Conflict, match="校验失败"):
        transfer.stage(
            directory,
            SimpleNamespace(),
            entry,
            canceled=lambda: False,
            progress=lambda _: None,
            chunk_size=4,
        )
    assert not partial.exists()
    final = transfer.stage(
        directory, None, entry, canceled=lambda: False, progress=lambda _: None, chunk_size=4
    )
    assert final.read_bytes() == data
