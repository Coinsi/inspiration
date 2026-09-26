"""Local video derivatives, with bounded input and provenance in generation jobs."""

import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from app.core.errors import CapabilityUnsupported
from app.models.storage import Blob
from app.modules.generation import jobs, media_engine
from app.storage import cas

MAX_BYTES = 256 * 1024 * 1024


def probe_file(path, canceled=lambda: False, *, max_duration_ms=1_800_000, input_format=None):
    log = media_engine.run(
        [
            "-protocol_whitelist",
            "file,pipe",
            *(["-f", input_format] if input_format else []),
            "-i",
            str(path),
            "-t",
            "0.05",
            "-f",
            "null",
            "-",
        ],
        canceled,
        timeout=30,
    ).split("Output #", 1)[0]
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", log)
    if not duration:
        raise ValueError("无法读取素材时长，请使用时长完整的视频文件")
    h, m, s = map(float, duration.groups())
    duration_ms = round((h * 3600 + m * 60 + s) * 1000)
    if not 0 < duration_ms <= max_duration_ms:
        raise ValueError(f"视频时长须在 {max_duration_ms // 60000} 分钟以内")
    video = re.search(r"Video: [^\n]*?\b(\d{2,5})x(\d{2,5})\b", log)
    dimensions = {}
    if video:
        width, height = map(int, video.groups())
        if width * height > 40_000_000 or max(width, height) > 8192:
            raise ValueError("视频分辨率过大，最长边须不超过 8192 像素")
        dimensions = {"width": width, "height": height}
    return {
        "duration_ms": duration_ms,
        "has_audio": "Audio:" in log,
        "has_video": bool(video),
        **dimensions,
    }


def source_bytes(db, ctx, gen_id):
    g = jobs.source(db, ctx.project.id, gen_id)
    jobs.validate_target(db, ctx.project.id, g.target_type, g.target_id)
    if g.output_type != "video":
        raise CapabilityUnsupported("请选择视频素材")
    blob = db.get(Blob, g.output_blob_hash)
    if not blob or blob.size_bytes > MAX_BYTES:
        raise CapabilityUnsupported("视频文件不存在或超过 256 MB")
    try:
        data = cas.read_bytes(blob)
    except Exception as exc:
        raise CapabilityUnsupported("无法读取源文件，请检查存储服务或重新上传视频") from exc
    if len(data) > MAX_BYTES:
        raise CapabilityUnsupported("视频文件超过 256 MB")
    return data


def metadata(db, ctx, gen_id):
    g = jobs.source(db, ctx.project.id, gen_id)
    jobs.validate_target(db, ctx.project.id, g.target_type, g.target_id)
    blob = db.get(Blob, g.output_blob_hash)
    if g.output_type != "video" or not blob or blob.size_bytes > MAX_BYTES:
        raise CapabilityUnsupported("请选择 256 MB 以内的视频素材")
    try:
        with cas.local_file(blob) as path:
            info = probe_file(path)
            if not info["has_video"]:
                raise ValueError("素材中没有可读取的视频画面")
            return info
    except ValueError as exc:
        raise CapabilityUnsupported(str(exc)) from exc


def validate_options(options, info=None):
    if options["operation"] == "frames":
        times = options.get("times_ms", [])
        if not 1 <= len(times) <= 12 or len(set(times)) != len(times):
            raise ValueError("请选择 1–12 个不同的抽帧时间点")
        if info and any(t >= info["duration_ms"] for t in times):
            raise ValueError("抽帧时间必须早于视频结束时间")
    else:
        start, end = options.get("start_ms", 0), options.get("end_ms")
        if end is None or end - start < 100:
            raise ValueError("请选择至少 0.1 秒的有效范围")
        if info and end > info["duration_ms"]:
            raise ValueError("所选范围超出视频时长")
        if info and options["operation"] == "audio" and not info["has_audio"]:
            raise ValueError("这个视频没有音轨，无法提取音频")


def submit(db, ctx, gen_id, data):
    g = jobs.source(db, ctx.project.id, gen_id)
    if g.output_type != "video":
        raise CapabilityUnsupported("请选择视频素材")
    if g.target_type == "timeline":
        from app.core.permissions import require

        require(ctx.role, "timeline.edit")
    options = data.model_dump()
    try:
        validate_options(options)
    except ValueError as exc:
        raise CapabilityUnsupported(str(exc)) from exc
    return jobs.local_job(
        db,
        ctx,
        g.target_type,
        g.target_id,
        "video_" + data.operation,
        {"source_generation_id": str(g.id), "source_blob": g.output_blob_hash, "options": options},
        {"frames": "image", "audio": "audio", "trim": "video"}[data.operation],
    )


def process(data, options, canceled=lambda: False):
    with process_files(data, options, canceled) as outputs:
        return [
            (p.read_bytes() if isinstance(p, Path) else p, typ, meta) for p, typ, meta in outputs
        ]


@contextmanager
def process_files(data, options, canceled=lambda: False):
    if (data.stat().st_size if isinstance(data, Path) else len(data)) > MAX_BYTES:
        raise ValueError("视频文件超过 256 MB")
    with tempfile.TemporaryDirectory(prefix="video-tool-") as tmp:
        root = Path(tmp)
        src = root / "source.mp4"
        src = media_engine.source_file(lambda _: data, None, src)
        info = probe_file(src, canceled)
        if not info["has_video"]:
            raise ValueError("素材中没有可读取的视频画面")
        validate_options(options, info)
        if options["operation"] == "frames":
            outputs = []
            for index, time_ms in enumerate(options["times_ms"]):
                dest = root / f"frame-{index}.png"
                media_engine.run(
                    [
                        "-ss",
                        str(time_ms / 1000),
                        "-protocol_whitelist",
                        "file,pipe",
                        "-i",
                        str(src),
                        "-map",
                        "0:v:0",
                        "-frames:v",
                        "1",
                        "-threads",
                        "2",
                        str(dest),
                    ],
                    canceled,
                    timeout=45,
                )
                if not dest.exists() or not dest.stat().st_size:
                    raise ValueError("所选时间点没有可解码的画面，请将时间点稍微提前")
                frame, dimensions = media_engine.validate_output(dest.read_bytes(), "image")
                outputs.append((frame, "image", {**dimensions, "source_time_ms": time_ms}))
            yield outputs
            return
        audio = options["operation"] == "audio"
        dest = root / ("audio.m4a" if audio else "segment.mp4")
        duration = options["end_ms"] - options["start_ms"]
        args = [
            "-ss",
            str(options["start_ms"] / 1000),
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(src),
            "-t",
            str(duration / 1000),
        ]
        if audio:
            args += ["-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "192k"]
        else:
            args += [
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
            ]
        media_engine.run(args + ["-threads", "2", "-movflags", "+faststart", str(dest)], canceled)
        result_info = probe_file(dest, canceled)
        if abs(result_info["duration_ms"] - duration) > 250:
            raise ValueError("导出时长与所选范围不符，请检查源文件是否完整")
        if dest.stat().st_size > MAX_BYTES:
            raise ValueError("导出文件超过 256 MB，请缩短所选范围")
        yield [(dest, "audio" if audio else "video", result_info)]
