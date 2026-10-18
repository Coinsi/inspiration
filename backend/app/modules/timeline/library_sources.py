"""Pin ready library media for editing without creating a storyboard shot."""

from sqlalchemy import select

from app.core.errors import Conflict
from app.models.generation import Generation, GenerationJob
from app.modules.library import service as library
from app.modules.timeline import service


def attach(db, ctx, timeline_id, version_id):
    timeline = service._get_timeline(db, ctx.project.id, timeline_id)
    item, version = library.version(db, ctx.project.id, version_id, lock=True)
    if version.status != "ready" or not version.original_hash:
        raise Conflict("请等待素材处理完成后再添加")
    existing = db.scalar(
        select(Generation).where(
            Generation.project_id == ctx.project.id,
            Generation.target_type == "timeline",
            Generation.target_id == timeline.id,
            Generation.input_refs["operation"].astext == "timeline_library_source",
            Generation.input_refs["library_origin"]["version_id"].astext == str(version.id),
        )
    )
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
    if not existing:
        job = GenerationJob(
            project_id=ctx.project.id,
            target_type="timeline",
            target_id=timeline.id,
            provider="local",
            request_type=version.media_type,
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
            target_id=timeline.id,
            provider="local",
            output_type=version.media_type,
            output_blob_hash=version.original_hash,
            cost_points=0,
            input_refs=refs,
        )
        db.add(existing)
        db.flush()
    return {
        "id": str(existing.id),
        "name": item.name,
        "media_type": version.media_type,
        "duration_ms": version.duration_ms,
    }
