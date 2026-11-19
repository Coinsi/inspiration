"""Resolve editable project media and pin immutable files without copying them."""

from sqlalchemy import select

from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.generation import Generation, GenerationJob
from app.models.storage import Blob
from app.modules.attachments import service as attachments
from app.modules.generation import references
from app.modules.library import service as library
from app.modules.timeline import service


def attach(db, ctx, timeline_id, version_id):
    return attach_source(db, ctx, timeline_id, "media_version", version_id)


def attach_source(db, ctx, timeline_id, source_type, source_id):
    service._get_timeline(db, ctx.project.id, timeline_id)
    if source_type == "generation":
        source = references.resolve(
            db, ctx.project.id, {"generation_id": source_id}, allow_audio=True
        )
        blob = db.get(Blob, source["blob_hash"])
        return {
            "id": str(source_id),
            "name": source["title"],
            "media_type": source["kind"],
            "duration_ms": _duration(blob, source["kind"], None),
        }
    if source_type == "attachment":
        item = attachments.get(db, ctx, source_id, lock=True)
        if not item.saved_at or item.hidden_at:
            raise NotFound("此附件未保存在素材库中")
        if item.status != "ready" or not item.blob_hash:
            raise Conflict("请等待附件上传完成后再添加")
        refs = {
            "operation": "timeline_attachment_source",
            "attachment_id": str(item.id),
            "filename": item.title,
        }
        return _pin(
            db,
            ctx,
            timeline_id,
            item.kind,
            item.blob_hash,
            item.thumbnail_hash,
            item.duration_ms,
            refs,
            Generation.input_refs["attachment_id"].astext == str(item.id),
        )
    return _attach_version(db, ctx, timeline_id, source_id)


def _attach_version(db, ctx, timeline_id, version_id):
    item, version = library.version(db, ctx.project.id, version_id, lock=True)
    if version.status != "ready" or not version.original_hash:
        raise Conflict("请等待素材处理完成后再添加")
    refs = {
        "operation": "timeline_library_source",
        "filename": item.name,
        "library_origin": {
            "media_id": str(item.id),
            "version_id": str(version.id),
            "media_type": version.media_type,
            "ordinal": version.ordinal,
            "name": item.name,
            "start_ms": 0,
            "end_ms": version.duration_ms,
        },
    }
    return _pin(
        db,
        ctx,
        timeline_id,
        version.media_type,
        version.original_hash,
        version.poster_hash,
        version.duration_ms,
        refs,
        Generation.input_refs["library_origin"]["version_id"].astext == str(version.id),
    )


def _duration(blob, kind, known):
    if not blob or not blob.mime.startswith(kind + "/"):
        raise Conflict("素材原文件不可用，请检查后重新选择")
    if kind == "image":
        return None
    if not blob.duration_ms:
        if not known:
            from app.modules.library.media_ingest import probe_media
            from app.storage import cas

            try:
                with cas.local_file(blob) as path:
                    known = probe_media(path, kind)["duration_ms"]
            except (ValueError, OSError) as exc:
                raise CapabilityUnsupported("读不到这份素材的时长，请检查原文件") from exc
        blob.duration_ms = known
    return known or blob.duration_ms


def _pin(db, ctx, timeline_id, kind, digest, poster, duration, refs, identity):
    # The source row is locked by the caller, so two simultaneous adds reuse one pin.
    duration = _duration(db.get(Blob, digest), kind, duration)
    existing = db.scalar(
        select(Generation).where(
            Generation.project_id == ctx.project.id,
            Generation.target_type == "timeline",
            Generation.target_id == timeline_id,
            Generation.input_refs["operation"].astext == refs["operation"],
            identity,
        )
    )
    if not existing:
        job = GenerationJob(
            project_id=ctx.project.id,
            target_type="timeline",
            target_id=timeline_id,
            provider="local",
            request_type=kind,
            status="succeeded",
            estimated_cost=0,
            actual_cost=0,
            created_by=ctx.user.id,
            input_snapshot=refs,
        )
        db.add(job)
        db.flush()
        existing = Generation(
            project_id=ctx.project.id,
            job_id=job.id,
            target_type="timeline",
            target_id=timeline_id,
            provider="local",
            output_type=kind,
            output_blob_hash=digest,
            thumbnail_hash=poster,
            cost_points=0,
            input_refs=refs,
        )
        db.add(existing)
        db.flush()
    return {
        "id": str(existing.id),
        "name": refs["filename"],
        "media_type": kind,
        "duration_ms": duration,
    }
