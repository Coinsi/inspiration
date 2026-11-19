"""Small time-indexed editing previews, computed once per source outside web requests."""

import base64
import io
import logging
import math
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.database import SessionLocal
from app.models.storage import Blob, TimelineMediaPreview
from app.modules.generation import media_engine
from app.modules.library.media_ingest import probe_media
from app.storage import cas

log = logging.getLogger(__name__)


def lookup(db, blob, *, worker_enabled):
    row = db.get(TimelineMediaPreview, blob.hash)
    if row and row.payload:
        return {"status": "ready", **row.payload}
    if not worker_enabled:
        return {"status": "unavailable"}
    if row is None:
        db.execute(
            insert(TimelineMediaPreview).values(source_hash=blob.hash).on_conflict_do_nothing()
        )
        return {"status": "pending"}
    return {"status": "pending" if row.due_at else "unavailable"}


def build(blob, source, folder, canceled):
    kind = blob.mime.split("/")[0]
    ms = blob.duration_ms or probe_media(source, kind, canceled)["duration_ms"]
    if ms <= 0:
        raise ValueError("Missing media duration")
    args = ["-protocol_whitelist", "file,pipe"]
    if kind == "audio":
        # Stream the audio and write only the peak amplitude of each equal-length window.
        stats = folder / "peaks.txt"
        bucket = max(1, math.ceil(ms * 8 / 640))
        media_engine.run(
            [
                *args,
                "-i",
                str(source),
                "-vn",
                "-af",
                f"aformat=channel_layouts=mono,aresample=8000,asetnsamples=n={bucket}:p=1,"
                "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.Peak_level:file=peaks.txt",
                "-f",
                "null",
                "-",
            ],
            canceled,
            timeout=120,
            cwd=folder,
        )
        levels = [
            float(line.split("=", 1)[1])
            for line in stats.read_text().splitlines()
            if line.startswith("lavfi.astats.Overall.Peak_level=")
        ]
        peaks = [
            round(255 * 10 ** (min(0, level) / 20)) if math.isfinite(level) else 0
            for level in levels
        ]
        return {"duration_ms": ms, "peaks": peaks}
    count = min(8, max(1, math.ceil(ms / 500)))
    with Image.new("RGB", (96 * count, 54)) as strip:
        for i in range(count):
            frame = folder / f"frame{i}.jpg"
            at = min(ms * i / count, max(0, ms - 100)) / 1000
            media_engine.run(
                [
                    "-ss",
                    str(at),
                    *args,
                    "-i",
                    str(source),
                    "-an",
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=160:90:force_original_aspect_ratio=decrease",
                    str(frame),
                ],
                canceled,
                timeout=25,
            )
            with Image.open(frame) as image:
                strip.paste(ImageOps.fit(image, (96, 54)), (i * 96, 0))
        output = io.BytesIO()
        strip.save(output, "JPEG", quality=70)
    return {
        "duration_ms": ms,
        "frames": count,
        "strip": "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii"),
    }


def run_once(canceled):
    with SessionLocal() as db:
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(726381915)")):
            return False
        row = db.scalar(
            select(TimelineMediaPreview)
            .where(TimelineMediaPreview.due_at <= func.now())
            .order_by(TimelineMediaPreview.due_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return False
        try:
            blob = db.get(Blob, row.source_hash)
            with tempfile.TemporaryDirectory(prefix="inspiration-timeline-") as tmp:
                with cas.local_file(blob, canceled, temp_dir=tmp) as source:
                    row.payload = build(blob, source, Path(tmp), canceled)
            row.due_at = None
        except media_engine.Canceled:
            return False
        except Exception as exc:
            log.warning(
                "Could not prepare timeline preview %s (%s)",
                row.source_hash[:12],
                type(exc).__name__,
            )
            row.attempts += 1
            row.due_at = (
                datetime.now(UTC) + timedelta(seconds=30 * row.attempts)
                if row.attempts < 3
                else None
            )
        db.commit()
        return True
