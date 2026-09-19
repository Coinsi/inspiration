"""Map reviewed original-media captions to an unsaved cut without modifying it."""

import uuid

from sqlalchemy import select

from app.core.errors import CapabilityUnsupported
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaAnnotation
from app.modules.generation import jobs
from app.modules.media.timing import duration
from app.modules.timeline import service


def preview(db, ctx, timeline_id, items):
    service._get_timeline(db, ctx.project.id, timeline_id)
    documents = [item.model_dump() for item in items]
    try:
        total = duration(documents)
    except ValueError as exc:
        raise CapabilityUnsupported(str(exc)) from exc
    if total > 1_800_000:
        raise CapabilityUnsupported("剪辑不能超过30分钟")
    offset, matched = 0, 0
    cues, cache, skipped = [], {}, []
    for index, item in enumerate(items):
        shot = jobs.validate_target(db, ctx.project.id, "shot", item.shot_id)
        gen_id = item.generation_id or shot.selected_generation_id
        span = item.out_point_ms - item.in_point_ms if item.out_point_ms else item.duration_ms
        if span <= 0:
            raise CapabilityUnsupported("请先为每个画面设置有效时长")
        generation = jobs.source(db, ctx.project.id, gen_id) if gen_id else None
        if generation and (generation.target_type != "shot" or generation.target_id != shot.id):
            raise CapabilityUnsupported("画面素材不属于所选镜头")
        origin = (generation.input_refs or {}).get("library_origin") if generation else None
        added = False
        if origin and generation.output_type == "video":
            version_id = uuid.UUID(origin["version_id"])
            if version_id not in cache:
                cache[version_id] = list(
                    db.scalars(
                        select(MediaAnnotation)
                        .join(MediaVersion)
                        .join(LibraryMedia)
                        .where(
                            LibraryMedia.project_id == ctx.project.id,
                            MediaAnnotation.version_id == version_id,
                            MediaAnnotation.kind == "speech",
                            MediaAnnotation.source["reviewed"].as_boolean().is_(True),
                            MediaAnnotation.source["transcription_id"].astext.is_not(None),
                        )
                        .order_by(MediaAnnotation.start_ms)
                        .limit(10001)
                    )
                )
                if len(cache[version_id]) > 10000:
                    raise CapabilityUnsupported("原片字幕超过10000条，请分段处理")
            source_start = origin["start_ms"] + item.in_point_ms
            source_end = min(source_start + span, origin["end_ms"])
            for annotation in cache[version_id]:
                begin = max(annotation.start_ms, source_start)
                end = min(annotation.end_ms, source_end)
                if end - begin < 100:
                    continue
                cues.append(
                    {
                        "start_ms": offset + begin - source_start,
                        "end_ms": offset + end - source_start,
                        "text": annotation.text,
                        "source": {
                            "kind": "reviewed_transcription",
                            "version_id": str(version_id),
                            "annotation_id": str(annotation.id),
                            "generation_id": str(generation.id),
                            "start_ms": begin,
                            "end_ms": end,
                        },
                    }
                )
                added = True
        if added:
            matched += 1
        else:
            skipped.append(index + 1)
        offset += span - int((item.transition or {}).get("duration_ms", 0))
    if len(cues) > 10000 or sum(len(cue["text"]) for cue in cues) > 1_000_000:
        raise CapabilityUnsupported("映射后的字幕过多，请拆分剪辑")
    return {
        "items": sorted(cues, key=lambda cue: (cue["start_ms"], cue["end_ms"])),
        "matched_clips": matched,
        "skipped_clips": skipped,
    }
