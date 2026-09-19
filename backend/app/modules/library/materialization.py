"""Turn pinned library ranges into independent, traceable creative media."""

import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.generation import GenerationJob
from app.models.identity import Project
from app.models.library import MediaUsage
from app.models.shot import Shot
from app.models.storage import Blob
from app.modules.generation import jobs, media_engine
from app.modules.generation.video_tools import probe_file
from app.modules.library import service
from app.storage import cas


def validate_snapshot(db, project_id, snapshot):
    origin = snapshot["library_origin"]
    _, version = service.version(db, project_id, uuid.UUID(origin["version_id"]), lock=True)
    if version.status != "ready" or version.original_hash != snapshot["source_blob"]:
        raise Conflict("原视频版本不可用，请回到素材库检查")
    return version


def submit(db, ctx, usage_id, data):
    # Request keys are project scoped, including requests for different source videos.
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    usage = db.get(MediaUsage, usage_id)
    if not usage:
        raise NotFound("片段引用不存在")
    item, version = service.version(db, ctx.project.id, usage.version_id, lock=True)
    # Reload after the root lock: a concurrent unlink may have completed while waiting.
    db.expire(usage)
    usage = db.scalar(select(MediaUsage).where(MediaUsage.id == usage_id))
    if not usage:
        raise NotFound("片段引用已解除")
    shot = db.scalar(select(Shot).where(Shot.id == usage.shot_id).with_for_update())
    if not shot or shot.project_id != ctx.project.id or shot.deleted_at:
        raise NotFound("镜头不存在")
    if shot.status == "locked":
        raise Conflict("镜头已锁定，请先解锁")
    if version.status != "ready" or not version.original_hash:
        raise Conflict("请等待原视频处理完成")
    time_ms = data.time_ms if data.time_ms is not None else (usage.start_ms + usage.end_ms) // 2
    if data.output_type == "image" and not usage.start_ms <= time_ms < usage.end_ms:
        raise CapabilityUnsupported("参考帧时间须在引用片段范围内")
    if data.output_type != "image" and usage.end_ms - usage.start_ms > 60_000:
        raise CapabilityUnsupported("请先将引用范围缩短到 60 秒以内，再制作视频或音轨")
    options = {
        "output_type": data.output_type,
        "time_ms": time_ms if data.output_type == "image" else None,
    }
    existing = db.scalar(
        select(GenerationJob).where(
            GenerationJob.project_id == ctx.project.id,
            GenerationJob.input_snapshot["library_origin"]["request_key"].astext
            == str(data.request_key),
            GenerationJob.input_snapshot["retry_of"].astext.is_(None),
        )
    )
    if existing:
        if (
            existing.input_snapshot["library_origin"]["usage_id"] != str(usage_id)
            or existing.input_snapshot["options"] != options
        ):
            raise Conflict("同一请求标识不能用于不同素材操作")
        return existing
    return jobs.local_job(
        db,
        ctx,
        "shot",
        shot.id,
        "library_materialize",
        {
            "source_blob": version.original_hash,
            "library_origin": {
                "request_key": str(data.request_key),
                "usage_id": str(usage.id),
                "media_id": str(item.id),
                "version_id": str(version.id),
                "ordinal": version.ordinal,
                "name": item.name,
                "start_ms": usage.start_ms,
                "end_ms": usage.end_ms,
            },
            "options": options,
        },
        data.output_type,
    )


def process(db, snapshot, canceled):
    blob = db.get(Blob, snapshot["source_blob"])
    if not blob:
        raise ValueError("原视频文件不存在")
    origin, options = snapshot["library_origin"], snapshot["options"]
    kind = options["output_type"]
    with tempfile.TemporaryDirectory(prefix="library-clip-") as tmp:
        root = Path(tmp)
        # Local CAS can be sought directly. Object storage streams to disk in 1 MiB blocks.
        src = Path(blob.storage_uri)
        if not src.is_file():
            src = root / "source.mp4"
            with cas.open_range(blob) as stream, src.open("wb") as target:
                while chunk := stream.read(1024 * 1024):
                    if canceled():
                        raise media_engine.Canceled()
                    target.write(chunk)
        info = probe_file(src, canceled, max_duration_ms=21_600_000)
        if not info["has_video"]:
            raise ValueError("原文件没有可读取的视频画面")
        if kind == "audio" and not info["has_audio"]:
            raise ValueError("这个视频没有音轨，无法提取音频")
        start = options["time_ms"] if kind == "image" else origin["start_ms"]
        if start >= info["duration_ms"] or origin["end_ms"] > info["duration_ms"] + 100:
            raise ValueError("引用范围超出原视频实际时长")
        dest = root / {"image": "frame.png", "video": "clip.mp4", "audio": "audio.m4a"}[kind]
        args = ["-ss", str(start / 1000), "-protocol_whitelist", "file,pipe", "-i", str(src)]
        if kind == "image":
            args += [
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-vf",
                "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease",
            ]
        else:
            args += ["-t", str((origin["end_ms"] - start) / 1000)]
            if kind == "video":
                args += [
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-vf",
                    "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                ]
            else:
                args += ["-map", "0:a:0", "-vn"]
            args += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
        media_engine.run([*args, "-threads", "2", str(dest)], canceled, timeout=180)
        if not dest.is_file() or not 0 < dest.stat().st_size <= 128 * 1024 * 1024:
            raise ValueError("未产生有效文件或输出超过 128 MB，请缩短片段范围")
        details = {"source_start_ms": start, "source_end_ms": origin["end_ms"]}
        if kind == "image":
            output, dimensions = media_engine.validate_output(dest.read_bytes(), "image")
            return output, kind, {**details, **dimensions}
        details.update(probe_file(dest, canceled))
        return dest.read_bytes(), kind, details
