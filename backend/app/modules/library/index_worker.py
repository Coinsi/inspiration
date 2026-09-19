"""Resumable sampled-video indexing, publishing only a complete run."""

import base64
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select, text

from app.core.database import SessionLocal, engine
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaIndex, MediaSegment
from app.models.storage import Blob
from app.modules.generation import media_engine
from app.modules.library import indexing
from app.storage import cas

QUEUE_LOCK = 726381906


def process(index_id, stopped=lambda: False):
    with SessionLocal() as db:
        run = db.get(MediaIndex, index_id)
        if not run or run.status not in ("queued", "processing"):
            return
        v = db.get(MediaVersion, run.version_id)
        run.status, run.error = "processing", None
        starts = [t for t in range(0, v.duration_ms, 8000) if v.duration_ms - t >= 100]
        run.total = len(starts)
        db.commit()

        def canceled():
            if stopped():
                return True
            with SessionLocal() as check:
                current = check.get(MediaIndex, index_id)
                return current.cancel_requested

        try:
            with tempfile.TemporaryDirectory(prefix="media-index-") as tmp:
                root = Path(tmp)
                source = root / "preview.mp4"
                with (
                    cas.open_range(db.get(Blob, v.proxy_hash)) as stream,
                    source.open("wb") as target,
                ):
                    shutil.copyfileobj(stream, target, 1024 * 1024)
                for ordinal, start in enumerate(starts):
                    if canceled():
                        raise media_engine.Canceled()
                    if db.scalar(
                        select(MediaSegment.id).where(
                            MediaSegment.index_id == run.id, MediaSegment.ordinal == ordinal
                        )
                    ):
                        continue
                    end = min(start + 10000, v.duration_ms)
                    times = list(range(start, end, 2000))
                    frames, encoded = [], []
                    for n, timestamp in enumerate(times):
                        path = root / f"frame-{n}.jpg"
                        media_engine.run(
                            [
                                "-ss",
                                str(timestamp / 1000),
                                "-i",
                                str(source),
                                "-frames:v",
                                "1",
                                "-vf",
                                "scale=w='min(384,iw)':h='min(256,ih)':force_original_aspect_ratio=decrease",
                                "-update",
                                "1",
                                str(path),
                            ],
                            canceled=canceled,
                            timeout=30,
                        )
                        data = path.read_bytes()
                        blob = cas.put_bytes(db, data, "image/jpeg")
                        frames.append({"at_ms": timestamp, "hash": blob.hash})
                        encoded.append(base64.b64encode(data).decode())
                    _, vector = indexing.embed(
                        {"images": encoded, "video": True, "fps": 0.5}, run.model_key
                    )
                    if canceled():
                        raise media_engine.Canceled()
                    db.add(
                        MediaSegment(
                            index_id=run.id,
                            ordinal=ordinal,
                            start_ms=start,
                            end_ms=end,
                            frames=frames,
                            embedding=vector,
                        )
                    )
                    db.flush()
                    run.completed = ordinal + 1
                    db.commit()
                # Publish under the same media lock as cancellation/trash and new-index submission.
                db.scalar(
                    select(LibraryMedia).where(LibraryMedia.id == v.media_id).with_for_update()
                )
                db.refresh(run)
                run.status = "canceled" if run.cancel_requested else "ready"
                db.commit()
        except Exception as exc:
            db.rollback()
            run = db.get(MediaIndex, index_id)
            run.status = (
                "canceled"
                if run.cancel_requested
                else ("queued" if isinstance(exc, media_engine.Canceled) else "failed")
            )
            run.error = (
                None
                if run.status != "failed"
                else "索引处理失败，请检查本地模型服务和视频预览后重建索引。"
            )
            db.commit()


def run_once(stopped=lambda: False):
    with engine.connect() as conn:
        if not conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": QUEUE_LOCK}):
            return False
        try:
            with SessionLocal() as db:
                id = db.scalar(
                    select(MediaIndex.id)
                    .where(MediaIndex.status.in_(["queued", "processing"]))
                    .order_by(MediaIndex.created_at)
                    .limit(1)
                )
            if id:
                process(id, stopped)
            return id is not None
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": QUEUE_LOCK})
            conn.commit()
