"""Restartable source staging for archive workers holding exclusive job ownership.

The staging directory is server-owned, never an uploaded/user-provided pathname.
Checkpoints are disposable caches: hash verification, not existence, proves completion.
"""

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

from app.core.errors import Conflict
from app.storage import cas


@contextmanager
def ownership(directory):
    """Process crash releases the OS lock; a second worker must not touch staging."""
    if directory is None:
        yield
        return
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "worker.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Conflict("该工程归档已有执行进程，请稍后重试") from exc
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def check(canceled):
    if canceled():
        from app.modules.generation.media_engine import Canceled

        raise Canceled()


def prepare(directory, envelope):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "manifest.json"
    data = json.dumps(envelope, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    if manifest.exists():
        if manifest.read_bytes() != data:
            raise Conflict("恢复任务的固定工程版本不一致，请创建新的导出任务")
    else:
        pending = directory / "manifest.pending"
        with pending.open("wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        pending.replace(manifest)
    return directory


def stage(directory, blob, entry, *, canceled, progress, chunk_size):
    check(canceled)
    final = directory / entry["hash"]
    partial = directory / (entry["hash"] + ".partial")
    if final.exists():
        verified = hashlib.sha256()
        with final.open("rb") as source:
            while chunk := source.read(chunk_size):
                check(canceled)
                verified.update(chunk)
        if final.stat().st_size == entry["size"] and verified.hexdigest() == entry["hash"]:
            progress(entry["size"])
            return final
        final.unlink()
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > entry["size"]:
        partial.unlink()
        offset = 0
    digest = hashlib.sha256()
    if offset:
        with partial.open("rb") as source:
            while chunk := source.read(chunk_size):
                check(canceled)
                digest.update(chunk)
    progress(offset)
    if offset < entry["size"]:
        with (
            cas.open_range(blob, offset, entry["size"] - offset) as source,
            partial.open("ab") as target,
        ):
            while offset < entry["size"]:
                check(canceled)
                chunk = source.read(min(chunk_size, entry["size"] - offset))
                if not chunk:
                    raise Conflict("媒体读取提前结束，可重试恢复导出")
                target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
                digest.update(chunk)
                offset += len(chunk)
                progress(offset)
    if offset != entry["size"] or digest.hexdigest() != entry["hash"]:
        partial.unlink(missing_ok=True)
        raise Conflict("暂存媒体校验失败，重试将重新读取该素材")
    check(canceled)
    partial.replace(final)
    return final
