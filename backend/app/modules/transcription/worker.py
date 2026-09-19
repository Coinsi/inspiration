"""A durable 30-second checkpoint queue. In-flight canceled results are discarded."""

import base64
import logging
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

from sqlalchemy import select, text

from app.core.database import SessionLocal, engine
from app.models.library import LibraryMedia, MediaVersion
from app.models.storage import Blob
from app.models.transcription import TranscriptionRun
from app.modules.generation import media_engine
from app.modules.transcription import schemas, service
from app.storage import cas

log = logging.getLogger(__name__)
QUEUE_LOCK = 726381909


def process(run_id, stopped=lambda: False):
    with SessionLocal() as db:
        run = db.get(TranscriptionRun, run_id)
        if not run or run.status not in ("queued", "processing"):
            return
        v = db.get(MediaVersion, run.version_id)
        duration = v.duration_ms
        run.status = "processing"
        run.error = None
        db.commit()

        def canceled():
            if stopped():
                return True
            with SessionLocal() as check:
                current = check.get(TranscriptionRun, run_id)
                return not current or current.cancel_requested

        try:
            with tempfile.TemporaryDirectory(prefix="inspiration-transcription-") as directory:
                root = Path(directory)
                source = root / "source.mp4"
                with (
                    cas.open_range(db.get(Blob, v.proxy_hash or v.original_hash)) as stream,
                    source.open("wb") as output,
                ):
                    shutil.copyfileobj(stream, output, 1024 * 1024)
                for ordinal in range(run.completed, run.total):
                    if canceled():
                        raise media_engine.Canceled()
                    start = ordinal * 30000
                    end = min(duration, start + 30000)
                    wav = root / "audio.wav"
                    try:
                        media_engine.run(
                            [
                                "-protocol_whitelist",
                                "file,pipe",
                                "-ss",
                                str(start / 1000),
                                "-i",
                                str(source),
                                "-t",
                                str((end - start) / 1000),
                                "-map",
                                "0:a:0",
                                "-vn",
                                "-ac",
                                "1",
                                "-ar",
                                "16000",
                                "-c:a",
                                "pcm_s16le",
                                str(wav),
                            ],
                            canceled=canceled,
                            timeout=60,
                        )
                    except ValueError as exc:
                        raise ValueError("音轨无法解码，请确认视频包含有效声音") from exc
                    result = service.inference(
                        "/transcribe",
                        {
                            "audio": base64.b64encode(wav.read_bytes()).decode(),
                            "language": run.language,
                        },
                    )
                    if result.get("model_key") != run.model_key:
                        raise ValueError("识别模型已变化，请重新建立转写任务")
                    rows = result.get("segments")
                    if not isinstance(rows, list) or len(rows) > 500:
                        raise ValueError("识别返回的字幕无效")
                    cues = []
                    for n, item in enumerate(rows):
                        s = max(0, int(item["start_ms"]))
                        e = min(end - start, int(item["end_ms"]))
                        content = str(item["text"]).strip()
                        if not content or e - s < 100:
                            continue
                        cue = schemas.Cue(
                            id=uuid.uuid5(run.id, f"{ordinal}:{n}"),
                            start_ms=start + s,
                            end_ms=start + e,
                            text=content,
                        ).model_dump(mode="json")
                        cues.append(cue)
                    if len(run.raw_cues) + len(cues) > 10000:
                        raise ValueError("字幕超过10000条，请按集分开转写")
                    # Publish checkpoint under the same root lock used by cancellation and deletion.
                    db.scalar(
                        select(LibraryMedia).where(LibraryMedia.id == v.media_id).with_for_update()
                    )
                    db.refresh(run)
                    if canceled():
                        raise media_engine.Canceled()
                    run.raw_cues = [*run.raw_cues, *cues]
                    run.cues = run.raw_cues
                    run.completed = ordinal + 1
                    db.commit()
                db.scalar(
                    select(LibraryMedia).where(LibraryMedia.id == v.media_id).with_for_update()
                )
                db.refresh(run)
                run.status = "canceled" if run.cancel_requested else "ready"
                db.commit()
        except Exception as exc:
            db.rollback()
            run = db.get(TranscriptionRun, run_id)
            run.status = (
                ("queued" if stopped() and not run.cancel_requested else "canceled")
                if isinstance(exc, media_engine.Canceled)
                else "failed"
            )
            run.error = (
                None
                if run.status != "failed"
                else (
                    str(exc)
                    if isinstance(exc, ValueError) and "请" in str(exc)
                    else "转写中断，已保留完成片段。检查音轨和识别服务后可续接。"
                )
            )
            db.commit()
            log.warning("Transcription %s ended: %s", run_id, run.status)


def run_once(stopped=lambda: False):
    with engine.connect() as conn:
        if not conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": QUEUE_LOCK}):
            return False
        try:
            with SessionLocal() as db:
                id = db.scalar(
                    select(TranscriptionRun.id)
                    .where(TranscriptionRun.status.in_(["queued", "processing"]))
                    .order_by(TranscriptionRun.created_at)
                    .limit(1)
                )
            if id:
                process(id, stopped)
            return id is not None
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": QUEUE_LOCK})
            conn.commit()


def start():
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                worked = run_once(stop.is_set)
            except Exception:
                worked = False
                log.warning("Transcription queue unavailable; check migrations")
            if not worked:
                stop.wait(2)

    thread = threading.Thread(target=loop, name="transcription-queue", daemon=True)
    thread.start()

    def shutdown():
        stop.set()
        thread.join(timeout=5)

    return shutdown
