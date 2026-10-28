"""Bounded, project-scoped media inspection; no arbitrary URLs and no paid calls.

Decoded bytes are returned as ephemeral model content parts, not persisted in
conversation history. The returned coverage describes what was supplied; it does
not claim that a model has understood or approved the material.
"""

import base64
import copy
import io
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy import select

from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.media_index import MediaAnnotation
from app.models.storage import Blob
from app.modules.generation import media_engine, video_tools
from app.modules.harness.context import ContextReference, revalidate_context
from app.storage import cas

MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_CONTENT_BYTES = 8 * 1024 * 1024
MAX_FRAMES = 4
MAX_AUDIO_MS = 15000


def _resolve(db, ctx, reference):
    ref = (
        reference
        if isinstance(reference, ContextReference)
        else ContextReference.model_validate(reference)
    )
    if ref.kind == "media_version":
        from app.modules.library.service import version

        item, row = version(db, ctx.project.id, ref.id)
        if row.status != "ready" or not row.original_hash:
            raise Conflict("素材仍在处理或没有可读取的原文件")
        blob = db.get(Blob, row.original_hash)
        name, typ, version_id = item.name, row.media_type, row.id
        meta = {
            "duration_ms": row.duration_ms,
            "width": row.width,
            "height": row.height,
            "has_audio": row.has_audio,
            "ordinal": row.ordinal,
        }
    elif ref.kind == "generation":
        from app.modules.generation import jobs

        row = jobs.source(db, ctx.project.id, ref.id)
        jobs.validate_target(db, ctx.project.id, row.target_type, row.target_id)
        blob = db.get(Blob, row.output_blob_hash)
        name, typ, version_id = "生成结果", getattr(row.output_type, "value", row.output_type), None
        meta = {}
    elif ref.kind == "attachment":
        from app.modules.attachments.service import resolve

        item = resolve(db, ctx, ref.id)
        blob = db.get(Blob, item["blob_hash"])
        name, typ, version_id = item["title"], item["kind"], None
        meta = {key: item.get(key) for key in ("duration_ms", "width", "height", "has_audio")}
    else:
        raise CapabilityUnsupported("媒体检查仅接受固定素材版本、对话附件或生成记录")
    if blob is None:
        raise NotFound("素材文件不存在")
    if ref.expected_blob_hash and ref.expected_blob_hash != blob.hash:
        raise Conflict("素材内容与固定引用不一致，不能自动切换到其他文件")
    if typ not in ("image", "audio", "video") or not blob.mime.startswith(typ + "/"):
        raise CapabilityUnsupported("素材类型与文件格式不一致")
    if typ == "image" and ref.start_ms is not None:
        raise CapabilityUnsupported("静态图片没有音视频时间范围")
    if typ == "audio" and ref.region is not None:
        raise CapabilityUnsupported("音频没有画面选择区域")
    for key in ("duration_ms", "width", "height"):
        if meta.get(key) is None:
            meta[key] = getattr(blob, key, None)
    if (
        ref.end_ms is not None
        and meta.get("duration_ms") is not None
        and ref.end_ms > meta["duration_ms"]
    ):
        raise CapabilityUnsupported("选择区间超出源素材时长")
    return (
        ref,
        blob,
        {
            "reference": {**ref.model_dump(mode="json"), "expected_blob_hash": blob.hash},
            "name": name,
            "media_type": typ,
            "mime": blob.mime,
            "size_bytes": blob.size_bytes,
            "metadata": meta,
            "observation": "metadata_only",
            "content_parts": [],
            "coverage": {"frames_ms": [], "audio_ranges": [], "transcript_ranges": []},
            "unobserved": ["visual_content", "audio_content", "creative_quality"],
        },
        version_id,
    )


def describe_media(db, ctx, reference):
    ctx = revalidate_context(db, ctx)
    return _resolve(db, ctx, reference)[2]


def _image_part(data, region=None):
    if len(data) > MAX_SOURCE_BYTES:
        raise CapabilityUnsupported("图片文件超过读取上限")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 40_000_000:
                raise CapabilityUnsupported("图片超过 4000 万像素")
            source.load()
            im = ImageOps.exif_transpose(source).convert("RGB")
            if region:
                x, y, w, h = region.x, region.y, region.width, region.height
                left, top = round(x * im.width), round(y * im.height)
                right, bottom = round((x + w) * im.width), round((y + h) * im.height)
                if right <= left or bottom <= top:
                    raise CapabilityUnsupported("选择区域小于一个像素")
                im = im.crop((left, top, right, bottom))
            im.thumbnail((1536, 1536))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=88)
            return {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode(),
                    "detail": "high",
                },
            }, {"width": im.width, "height": im.height}
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise CapabilityUnsupported("图片不能解码") from exc


def _transcript(db, version_id, start, end):
    if version_id is None:
        return [], False
    rows = list(
        db.scalars(
            select(MediaAnnotation)
            .where(
                MediaAnnotation.version_id == version_id,
                MediaAnnotation.kind == "speech",
                MediaAnnotation.end_ms > start,
                MediaAnnotation.start_ms < end,
            )
            .order_by(MediaAnnotation.start_ms, MediaAnnotation.id)
            .limit(201)
        )
    )
    result, chars = [], 0
    for row in rows[:200]:
        if chars + len(row.text) > 16000:
            return result, True
        # Do not clip transcript timestamps: text has not been word-aligned here.
        result.append(
            {
                "id": str(row.id),
                "start_ms": row.start_ms,
                "end_ms": row.end_ms,
                "text": row.text,
                "source": copy.deepcopy(row.source or {}),
                "trust": "quoted_transcript",
                "word_alignment_verified": False,
            }
        )
        chars += len(row.text)
    return result, len(rows) > 200


def inspect_media(
    db,
    ctx,
    reference,
    *,
    frame_times_ms=None,
    include_transcript=True,
    include_audio=False,
    canceled=lambda: False,
):
    """Decode one image, up to four video frames and/or 15s mono WAV.

    For audio, callers must provide an explicit short range unless the entire
    source is at most 15 seconds. Returned input_audio requires a capable model;
    a text-only adapter must reject it rather than pretending it listened.
    """
    ctx = revalidate_context(db, ctx)
    ref, blob, result, version_id = _resolve(db, ctx, reference)
    if blob.size_bytes > MAX_SOURCE_BYTES:
        raise CapabilityUnsupported("素材超过 256 MB，请先创建较小的可检查片段")
    times = list(frame_times_ms or [])
    if (
        len(times) > MAX_FRAMES
        or len(set(times)) != len(times)
        or any(type(t) is not int or t < 0 for t in times)
    ):
        raise CapabilityUnsupported("每次检查最多 4 个不同的非负整数毫秒时间点")
    typ = result["media_type"]
    if typ != "video" and times:
        raise CapabilityUnsupported("只有视频支持抽帧")
    if typ == "image":
        if include_audio:
            raise CapabilityUnsupported("图片没有音轨")
        try:
            part, dimensions = _image_part(cas.read_bytes(blob), ref.region)
        except OSError as exc:
            raise CapabilityUnsupported("图片文件无法读取，未标记为已观察") from exc
        result["content_parts"] = [part]
        result["coverage"]["image"] = {
            "region": ref.region.model_dump() if ref.region else None,
            "decoded_size": dimensions,
        }
        result["observation"] = "decoded_media_supplied"
        result["unobserved"] = ["creative_quality", "model_interpretation"]
        return result
    try:
        with cas.local_file(blob, canceled=canceled) as path:
            info = video_tools.probe_file(path, canceled)
            result["metadata"].update(info)
            start, end = (
                ref.start_ms or 0,
                ref.end_ms if ref.end_ms is not None else info["duration_ms"],
            )
            if end > info["duration_ms"]:
                raise CapabilityUnsupported("选择区间超出实际解码时长")
            if any(t < start or t >= end for t in times):
                raise CapabilityUnsupported("抽帧时间必须位于选中区间内")
            if typ == "video" and times:
                with video_tools.process_files(
                    path, {"operation": "frames", "times_ms": times}, canceled
                ) as frames:
                    for frame, _, meta in frames:
                        part, dimensions = _image_part(frame, ref.region)
                        result["content_parts"].extend(
                            [
                                {
                                    "type": "text",
                                    "text": (
                                        f"源素材时间 {meta['source_time_ms']} ms 的抽样帧；"
                                        "仅覆盖这一时刻。"
                                    ),
                                },
                                part,
                            ]
                        )
                        result["coverage"]["frames_ms"].append(meta["source_time_ms"])
                        result["coverage"]["decoded_size"] = dimensions
                    result["coverage"]["region"] = ref.region.model_dump() if ref.region else None
            if include_audio:
                if not info.get("has_audio"):
                    raise CapabilityUnsupported("这个素材没有可读取的音轨")
                if end - start > MAX_AUDIO_MS:
                    raise CapabilityUnsupported("单次试听最多 15 秒，请选择明确的音频区间")
                with tempfile.TemporaryDirectory(prefix="harness-audio-") as tmp:
                    dest = Path(tmp) / "sample.wav"
                    media_engine.run(
                        [
                            "-ss",
                            str(start / 1000),
                            "-protocol_whitelist",
                            "file,pipe",
                            "-i",
                            str(path),
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
                            "-threads",
                            "2",
                            str(dest),
                        ],
                        canceled,
                        timeout=45,
                    )
                    data = dest.read_bytes()
                result["content_parts"].append(
                    {
                        "type": "input_audio",
                        "input_audio": {"data": base64.b64encode(data).decode(), "format": "wav"},
                    }
                )
                result["coverage"]["audio_ranges"].append({"start_ms": start, "end_ms": end})
            if include_transcript:
                transcript, truncated = _transcript(db, version_id, start, end)
                result["transcript"] = transcript
                result["transcript_truncated"] = truncated
                result["coverage"]["transcript_ranges"] = [
                    {"start_ms": t["start_ms"], "end_ms": t["end_ms"]} for t in transcript
                ]
                if not transcript:
                    result["transcript_status"] = (
                        "no_existing_transcript; no_new_transcription_requested"
                    )
    except (OSError, ValueError, RuntimeError) as exc:
        raise CapabilityUnsupported(
            "素材解码失败，未标记为已观察", {"reason": str(exc)[:300]}
        ) from exc
    encoded_size = sum(
        len(p.get("image_url", {}).get("url", "")) + len(p.get("input_audio", {}).get("data", ""))
        for p in result["content_parts"]
    )
    if encoded_size > MAX_CONTENT_BYTES:
        raise CapabilityUnsupported("多模态内容超过上下文传输上限，请减少抽帧或试听范围")
    result["observation"] = (
        "decoded_media_supplied" if result["content_parts"] else "metadata_and_existing_text_only"
    )
    result["unobserved"] = ["creative_quality", "model_interpretation"]
    if typ == "video":
        result["unobserved"].append("motion_between_sampled_frames")
        if not times:
            result["unobserved"].append("visual_content")
    if not include_audio:
        result["unobserved"].append("audio_content")
    return result
