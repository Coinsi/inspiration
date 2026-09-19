"""Persistent local media queue. A PostgreSQL session lock permits one bounded encoder.

The lock survives commits and is released on worker/process death. A new worker
can then safely resume a 'processing' record left by its predecessor.
"""

import logging
import shutil
import tempfile
import threading
from pathlib import Path

from sqlalchemy import select, text

from app.core.database import SessionLocal, engine
from app.models.library import MediaVersion
from app.models.storage import Blob
from app.modules.generation import media_engine
from app.modules.generation.video_tools import probe_file
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
                    with cas.open_range(blob) as stream, source.open("wb") as output:
                        shutil.copyfileobj(stream, output, 1024 * 1024)
                with source.open("rb") as stream:
                    header = stream.read(16)
                if source.stat().st_size != v.size_bytes:
                    raise ValueError("源文件长度发生变化，请重新上传")
                if header[:4] == b"\x1a\x45\xdf\xa3":
                    input_format = "matroska"
                elif header[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free"):
                    input_format = "mov"
                else:
                    raise ValueError("不支持的容器，请重新上传")
                info = probe_file(
                    source, canceled, max_duration_ms=6 * 3600 * 1000, input_format=input_format
                )
                if not info["has_video"]:
                    raise ValueError("文件中没有可解码的视频画面")
                v.duration_ms, v.width, v.height = (
                    info["duration_ms"],
                    info["width"],
                    info["height"],
                )
                mime = {
                    ".mov": "video/quicktime",
                    ".mkv": "video/x-matroska",
                    ".webm": "video/webm",
                }.get(Path(v.filename).suffix.lower(), "video/mp4")
                original = cas.put_file(
                    db, source, mime, duration_ms=v.duration_ms, width=v.width, height=v.height
                )
                v.original_hash = original.hash
                db.commit()  # Original survives a failed proxy; retries reuse the same identity.
                proxy, poster = folder / "proxy.mp4", folder / "poster.jpg"
                media_engine.run(
                    [
                        "-protocol_whitelist",
                        "file,pipe",
                        "-f",
                        input_format,
                        "-i",
                        str(source),
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a:0?",
                        "-vf",
                        "scale=w='min(1280,iw)':h='min(720,ih)':"
                        "force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "28",
                        "-threads",
                        "2",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "96k",
                        "-movflags",
                        "+faststart",
                        str(proxy),
                    ],
                    canceled=canceled,
                    timeout=6 * 3600,
                )
                media_engine.run(
                    [
                        "-protocol_whitelist",
                        "file,pipe",
                        "-i",
                        str(proxy),
                        "-frames:v",
                        "1",
                        "-update",
                        "1",
                        str(poster),
                    ],
                    canceled=canceled,
                    timeout=30,
                )
                v.proxy_hash = cas.put_file(db, proxy, "video/mp4", duration_ms=v.duration_ms).hash
                v.poster_hash = cas.put_file(db, poster, "image/jpeg").hash
                v.status, v.error = "ready", None
                db.commit()
            staging_path(v.id).unlink(missing_ok=True)
        except Exception as exc:
            db.rollback()
            v = db.get(MediaVersion, version_id)
            v.status = "queued" if isinstance(exc, media_engine.Canceled) else "failed"
            # ffmpeg's detailed log includes local paths; keep user-facing diagnostics bounded.
            v.error = "视频预览处理失败，请检查文件是否可播放、剩余磁盘空间及存储服务，再重试。"
            if isinstance(exc, ValueError) and "请重新上传" in str(exc):
                v.error = "视频文件缺失、不完整或容器不支持，请取消此版本后重新上传。"
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
