"""Persistent local media queue. A PostgreSQL session lock permits one bounded encoder.

The lock survives commits and is released on worker/process death. A new worker
can then safely resume a 'processing' record left by its predecessor.
"""

import hashlib
import logging
import tempfile
import threading
from pathlib import Path

from sqlalchemy import select, text

from app.core.database import SessionLocal, engine
from app.models.library import MediaVersion
from app.models.storage import Blob
from app.modules.generation import media_engine
from app.modules.library.media_ingest import build_preview, check_canceled, probe_media
from app.modules.library.service import staging_path
from app.storage import cas

log = logging.getLogger(__name__)
QUEUE_LOCK = 726381905


def process(version_id, canceled=lambda: False):
    with SessionLocal() as db:
        v = db.get(MediaVersion, version_id)
        if not v or v.status not in ("queued", "processing"):
            return
        v.status, v.error = "processing", None
        db.commit()
        try:
            with tempfile.TemporaryDirectory(prefix="inspiration-proxy-") as tmp:
                folder = Path(tmp)
                source = staging_path(v.id)
                if not source.exists():
                    if not v.original_hash:
                        raise ValueError("原始暂存文件缺失，请重新上传")
                    source = folder / "source"
                    blob = db.get(Blob, v.original_hash)
                    if blob is None:
                        raise ValueError("原始素材记录缺失，请重新上传")
                    with cas.open_range(blob) as stream, source.open("wb") as output:
                        while chunk := stream.read(1024 * 1024):
                            check_canceled(canceled)
                            output.write(chunk)
                if source.stat().st_size != v.size_bytes:
                    raise ValueError("源文件长度发生变化，请重新上传")
                if v.original_hash:
                    digest = hashlib.sha256()
                    with source.open("rb") as stream:
                        while chunk := stream.read(1024 * 1024):
                            check_canceled(canceled)
                            digest.update(chunk)
                    if digest.hexdigest() != v.original_hash:
                        raise ValueError("原始素材内容发生变化，请重新上传")
                info = probe_media(source, v.media_type, canceled)
                v.has_audio = info["has_audio"]
                v.duration_ms, v.width, v.height = (
                    info["duration_ms"],
                    info["width"],
                    info["height"],
                )
                original = cas.put_file(
                    db,
                    source,
                    info["mime"],
                    duration_ms=v.duration_ms,
                    width=v.width,
                    height=v.height,
                )
                v.original_hash = original.hash
                db.commit()  # Keep immutable original even if deriving a preview fails.
                previews = build_preview(source, folder, info, canceled)
                path, mime, metadata = previews["proxy"]
                v.proxy_hash = cas.put_file(db, path, mime, **metadata).hash
                if previews["poster"]:
                    path, mime, metadata = previews["poster"]
                    v.poster_hash = cas.put_file(db, path, mime, **metadata).hash
                else:
                    v.poster_hash = None
                check_canceled(canceled)
                v.status, v.error = "ready", None
                db.commit()
            try:
                staging_path(v.id).unlink(missing_ok=True)
            except OSError:
                # The original and previews are already durable. A cleanup error
                # must not turn a completed import into a failed media version.
                log.warning("Library staging cleanup failed for version %s", version_id)
        except Exception as exc:
            db.rollback()
            v = db.get(MediaVersion, version_id)
            v.status = "queued" if isinstance(exc, media_engine.Canceled) else "failed"
            # ffmpeg's detailed log includes local paths; keep user-facing diagnostics bounded.
            v.error = "素材预览处理失败，请检查文件是否完整可读、剩余磁盘空间及存储服务，再重试。"
            if isinstance(exc, ValueError) and "请重新上传" in str(exc):
                v.error = "素材文件缺失、不完整、实际类型不符或格式不支持，请取消此版本后重新上传。"
            if isinstance(exc, media_engine.Canceled):
                v.error = None
            db.commit()
            log.warning("Library version %s failed: %s", version_id, type(exc).__name__)


def run_once(canceled=lambda: False):
    with engine.connect() as conn:
        if not conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": QUEUE_LOCK}):
            return False
        try:
            with SessionLocal() as db:
                version_id = db.scalar(
                    select(MediaVersion.id)
                    .where(MediaVersion.status.in_(("queued", "processing")))
                    .order_by(MediaVersion.created_at)
                    .limit(1)
                )
            if version_id:
                process(version_id, canceled)
            return version_id is not None
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": QUEUE_LOCK})
            conn.commit()


def start():
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                worked = run_once(stop.is_set)
                if not worked and not stop.is_set():
                    from app.modules.library.index_worker import run_once as index_once

                    worked = index_once(stop.is_set)
            except Exception:
                worked = False
                log.warning("Library queue unavailable; check database migrations and storage")
            if not worked:
                stop.wait(2)

    thread = threading.Thread(target=loop, name="library-queue", daemon=True)
    thread.start()

    def shutdown():
        stop.set()
        thread.join(timeout=5)

    return shutdown
