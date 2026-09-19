"""Atomic timeline documents: revision checks, source ownership and restorable history."""

import copy

from sqlalchemy import select

from app.core import audit
from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.storage import Blob
from app.models.timeline import AudioTrack, Timeline, TimelineRevision
from app.modules.timeline import schemas, service


def lock(db, project_id, timeline_id):
    t = db.scalar(
        select(Timeline)
        .where(
            Timeline.id == timeline_id,
            Timeline.project_id == project_id,
            Timeline.deleted_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not t:
        raise NotFound("时间线不存在")
    return t


def document(db, project_id, timeline_id):
    t = service._get_timeline(db, project_id, timeline_id)
    items = service.list_items(db, project_id, timeline_id)
    audio = db.scalars(
        select(AudioTrack)
        .where(AudioTrack.timeline_id == t.id)
        .order_by(AudioTrack.config["_ordinal"].as_integer().asc().nullslast(), AudioTrack.id)
    ).all()
    return {
        "revision": t.revision,
        "items": [
            schemas.TimelineItemIn.model_validate(it, from_attributes=True).model_dump(mode="json")
            for it in items
        ],
        "audio": [schemas.AudioIn.model_validate(a.config).model_dump(mode="json") for a in audio],
        "subtitles": copy.deepcopy(t.subtitles),
        "visuals": copy.deepcopy(t.visuals),
    }


def remember(db, t, user_id, label):
    exists = db.scalar(
        select(TimelineRevision.id).where(
            TimelineRevision.timeline_id == t.id,
            TimelineRevision.revision == t.revision,
        )
    )
    if not exists:
        db.add(
            TimelineRevision(
                timeline_id=t.id,
                revision=t.revision,
                snapshot=document(db, t.project_id, t.id),
                label=label,
                created_by=user_id,
            )
        )
        db.flush()


def save_document(db, ctx, timeline_id, data, label="保存剪辑"):
    from app.modules.generation import jobs

    t = lock(db, ctx.project.id, timeline_id)
    if data.revision != t.revision:
        raise Conflict("时间线已在其他页面修改。请保留当前草稿，重新载入后再编辑。")
    from app.modules.media.timing import duration

    try:
        total = duration([i.model_dump() for i in data.items])
    except ValueError as exc:
        raise CapabilityUnsupported(str(exc)) from exc
    if total > 1_800_000:
        raise CapabilityUnsupported("时间线最长30分钟")
    # Older clients updating other tracks must not silently erase visual layers.
    visuals = (
        data.visuals
        if "visuals" in data.model_fields_set
        else [schemas.VisualIn.model_validate(v) for v in t.visuals]
    )
    resolve_visuals(db, ctx, visuals, total)
    cues = [c.model_dump(mode="json", exclude_none=True) for c in data.subtitles]
    if any(
        c["end_ms"] - c["start_ms"] < 100
        or c["end_ms"] > total
        or not c["text"].strip()
        or "\ufffd" in c["text"]
        for c in cues
    ):
        raise CapabilityUnsupported("字幕须在时间线内，文字非空，持续至少0.1秒")
    if sum(len(c["text"]) for c in cues) > 1_000_000:
        raise CapabilityUnsupported("字幕内容过大")
    tracks = []
    for a in data.audio:
        g = jobs.source(db, ctx.project.id, a.generation_id)
        jobs.validate_target(db, ctx.project.id, g.target_type, g.target_id)
        if g.output_type != "audio":
            raise CapabilityUnsupported("音轨请选择音频素材")
        blob = db.get(Blob, g.output_blob_hash)
        if not blob or not blob.duration_ms:
            raise CapabilityUnsupported("音频缺少时长信息，请重新上传或提取")
        if a.in_point_ms + a.duration_ms > blob.duration_ms or a.start_ms + a.duration_ms > total:
            raise CapabilityUnsupported("音轨范围超出源音频或时间线时长")
        if a.fade_in_ms + a.fade_out_ms > a.duration_ms:
            raise CapabilityUnsupported("淡入与淡出总时长不能超过音频片段")
        tracks.append((a, blob))
    remember(db, t, ctx.user.id, "初始状态")
    service._replace_items(db, ctx, t.id, data.items)
    db.query(AudioTrack).filter(AudioTrack.timeline_id == t.id).delete()
    for ordinal, (a, blob) in enumerate(tracks):
        db.add(
            AudioTrack(
                timeline_id=t.id,
                kind=a.kind,
                blob_hash=blob.hash,
                config={**a.model_dump(mode="json"), "_ordinal": ordinal},
            )
        )
    t.subtitles = sorted(cues, key=lambda c: (c["start_ms"], c["end_ms"]))
    t.visuals = [v.model_dump(mode="json") for v in visuals]
    t.revision += 1
    db.flush()
    remember(db, t, ctx.user.id, label)
    audit.record(
        db,
        action="timeline.save",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="timeline",
        target_id=t.id,
        detail={"revision": t.revision, "label": label},
    )
    return document(db, ctx.project.id, t.id)


def history(db, ctx, timeline_id, offset=0):
    service._get_timeline(db, ctx.project.id, timeline_id)
    rows = db.scalars(
        select(TimelineRevision)
        .where(TimelineRevision.timeline_id == timeline_id)
        .order_by(TimelineRevision.revision.desc())
        .offset(offset)
        .limit(51)
    ).all()
    return {
        "items": [
            {
                "revision": r.revision,
                "label": r.label,
                "created_at": r.created_at,
                "clips": len(r.snapshot["items"]),
                "audio": len(r.snapshot["audio"]),
                "subtitles": len(r.snapshot["subtitles"]),
                "visuals": len(r.snapshot.get("visuals", [])),
            }
            for r in rows[:50]
        ],
        "has_more": len(rows) > 50,
    }


def restore(db, ctx, timeline_id, target_revision, current_revision):
    lock(db, ctx.project.id, timeline_id)
    row = db.scalar(
        select(TimelineRevision).where(
            TimelineRevision.timeline_id == timeline_id,
            TimelineRevision.revision == target_revision,
        )
    )
    if not row:
        raise NotFound("历史版本不存在")
    data = schemas.TimelineDocumentIn.model_validate(
        {**row.snapshot, "visuals": row.snapshot.get("visuals", []), "revision": current_revision}
    )
    return save_document(db, ctx, timeline_id, data, label=f"恢复到版本 {target_revision}")


def render_tracks(db, ctx, timeline_id):
    from app.modules.media.timing import duration

    doc = document(db, ctx.project.id, timeline_id)
    tracks = db.scalars(select(AudioTrack).where(AudioTrack.timeline_id == timeline_id)).all()
    return {
        "timeline_revision": doc["revision"],
        "subtitles": doc["subtitles"],
        "audio": [{**a.config, "blob_hash": a.blob_hash} for a in tracks],
        "visuals": resolve_visuals(
            db,
            ctx,
            [schemas.VisualIn.model_validate(v) for v in doc["visuals"]],
            duration(doc["items"]),
        ),
    }


def resolve_visuals(db, ctx, visuals, total):
    """Validate ownership and freeze media hashes at submission, including hidden layers."""
    from app.modules.generation import jobs

    if len({v.id for v in visuals}) != len(visuals):
        raise CapabilityUnsupported("叠加画面标识不能重复")
    result = []
    for v in visuals:
        if v.start_ms + v.duration_ms > total:
            raise CapabilityUnsupported("叠加画面不能超出时间线时长")
        if any(
            other.id != v.id
            and other.track == v.track
            and max(v.start_ms, other.start_ms)
            < min(v.start_ms + v.duration_ms, other.start_ms + other.duration_ms)
            for other in visuals
        ):
            raise CapabilityUnsupported("同一画面轨道不能重叠，请改用其他轨道")
        g = jobs.source(db, ctx.project.id, v.generation_id)
        jobs.validate_target(db, ctx.project.id, g.target_type, g.target_id)
        if g.output_type not in ("image", "video"):
            raise CapabilityUnsupported("叠加画面请选择图片或视频")
        blob = db.get(Blob, g.output_blob_hash)
        if not blob:
            raise CapabilityUnsupported("叠加素材不存在")
        if g.output_type == "video":
            if not blob.duration_ms:
                # Legacy uploads and rendered films may predate duration metadata.
                from app.modules.generation.video_tools import metadata

                blob.duration_ms = metadata(db, ctx, g.id)["duration_ms"]
            if v.in_point_ms + v.duration_ms > blob.duration_ms:
                raise CapabilityUnsupported("叠加片段超出源视频时长")
        result.append(
            {
                **v.model_dump(mode="json"),
                "blob_hash": g.output_blob_hash,
                "output_type": g.output_type,
            }
        )
    return result
