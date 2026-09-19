"""Bounded audio ingestion; original files stay immutable in the project media store."""

import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select

from app.core import audit
from app.core.errors import CapabilityUnsupported
from app.models.generation import Generation, GenerationJob
from app.models.storage import Blob
from app.modules.generation import jobs
from app.modules.generation.video_tools import probe_file
from app.modules.timeline import service
from app.storage import cas

FORMATS = {
    ".wav": ("wav", "audio/wav"),
    ".mp3": ("mp3", "audio/mpeg"),
    ".m4a": ("mov", "audio/mp4"),
    ".ogg": ("ogg", "audio/ogg"),
    ".flac": ("flac", "audio/flac"),
}


def upload(db, ctx, timeline_id, file):
    service._get_timeline(db, ctx.project.id, timeline_id)
    fmt = FORMATS.get(Path(file.filename or "").suffix.lower())
    if not fmt:
        raise CapabilityUnsupported("支持 WAV、MP3、M4A、OGG、FLAC 音频")
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if not 0 < size <= 100 * 1024 * 1024:
        raise CapabilityUnsupported("音频须在100 MB以内")
    with tempfile.TemporaryDirectory(prefix="inspiration-audio-") as tmp:
        path = Path(tmp) / "source"
        with path.open("wb") as output:
            shutil.copyfileobj(file.file, output, 1024 * 1024)
        try:
            info = probe_file(path, input_format=fmt[0])
            if not info["has_audio"]:
                raise ValueError("文件中没有可解码音频")
        except ValueError as exc:
            raise CapabilityUnsupported("无法读取音频，请检查文件格式与时长（最长30分钟）") from exc
        blob = cas.put_file(db, path, fmt[1], duration_ms=info["duration_ms"])
    name = Path(file.filename or "音频").name[:255]
    job = GenerationJob(
        project_id=ctx.project.id,
        target_type="timeline",
        target_id=timeline_id,
        provider="local",
        request_type="audio",
        status="succeeded",
        estimated_cost=0,
        actual_cost=0,
        created_by=ctx.user.id,
        input_snapshot={"operation": "audio_upload", "filename": name},
    )
    jobs.event(job, "succeeded", "音频上传完成")
    db.add(job)
    db.flush()
    gen = Generation(
        project_id=ctx.project.id,
        job_id=job.id,
        target_type="timeline",
        target_id=timeline_id,
        provider="local",
        output_type="audio",
        output_blob_hash=blob.hash,
        cost_points=0,
        input_refs={"operation": "audio_upload", "filename": name},
    )
    db.add(gen)
    db.flush()
    audit.record(
        db,
        action="timeline.audio_upload",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="timeline",
        target_id=timeline_id,
        detail={"generation_id": str(gen.id)},
    )
    return {
        "id": str(gen.id),
        "name": name,
        "blob_hash": blob.hash,
        "duration_ms": blob.duration_ms,
    }


def sources(db, ctx, offset=0):
    rows = db.execute(
        select(Generation, Blob)
        .join(Blob, Blob.hash == Generation.output_blob_hash)
        .where(Generation.project_id == ctx.project.id, Generation.output_type == "audio")
        .order_by(Generation.created_at.desc(), Generation.id)
        .offset(offset)
        .limit(51)
    ).all()
    return {
        "items": [
            {
                "id": str(g.id),
                "name": (g.input_refs or {}).get("filename") or "提取的音频",
                "blob_hash": b.hash,
                "duration_ms": b.duration_ms,
            }
            for g, b in rows[:50]
        ],
        "has_more": len(rows) > 50,
    }
