"""The generated collection indexes durable outputs without copying their files.

Using Generation as the collection entry makes publication atomic with job success,
includes historical results and keeps every existing media reference stable.
"""

from sqlalchemy import func, or_, select

from app.core.errors import NotFound
from app.models.generation import Generation, GenerationJob
from app.models.storage import Blob


def query_for(project_id):
    return (
        select(Generation, GenerationJob, Blob)
        .join(GenerationJob, Generation.job_id == GenerationJob.id)
        .join(Blob, Generation.output_blob_hash == Blob.hash)
        .where(
            Generation.project_id == project_id,
            GenerationJob.project_id == project_id,
            GenerationJob.status == "succeeded",
            Generation.output_type.in_(("image", "video")),
            Generation.provider.not_in(("upload", "local", "library")),
        )
    )


def entry(db, g, job, blob):
    from app.core.errors import AppError
    from app.modules.generation.jobs import validate_target

    reference_available = g.output_type == "image"
    if reference_available:
        try:
            validate_target(db, g.project_id, g.target_type, g.target_id)
        except AppError:
            reference_available = False
    profile = (job.input_snapshot or {}).get("model_profile") or {}
    routing = (g.input_refs or {}).get("routing") or {}
    return {
        "id": g.id,
        "job_id": job.id,
        "title": (g.prompt_snapshot or "AI 生成素材")[:120],
        "prompt": g.prompt_snapshot,
        "output_type": g.output_type,
        "blob_hash": blob.hash,
        "model_name": routing.get("model_name")
        or profile.get("display_name")
        or g.model
        or g.provider,
        "provider": g.provider,
        "created_at": g.created_at,
        "width": blob.width,
        "height": blob.height,
        "duration_ms": blob.duration_ms,
        "size_bytes": blob.size_bytes,
        "target_type": g.target_type,
        "target_id": g.target_id,
        "reference_available": reference_available,
    }


def catalog(db, project_id, *, query="", kind=None, offset=0, limit=24):
    stmt = query_for(project_id)
    if kind:
        stmt = stmt.where(Generation.output_type == kind)
    if query.strip():
        stmt = stmt.where(
            or_(
                Generation.prompt_snapshot.icontains(query.strip(), autoescape=True),
                Generation.model.icontains(query.strip(), autoescape=True),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(Generation.created_at.desc(), Generation.id).offset(offset).limit(limit)
    ).all()
    return {
        "items": [entry(db, *row) for row in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def detail(db, project_id, generation_id):
    row = db.execute(query_for(project_id).where(Generation.id == generation_id)).first()
    if row is None:
        raise NotFound("生成素材不存在")
    return entry(db, *row)
