"""Bounded, read-only task browsing; execution continues through existing job commands."""

from sqlalchemy import String, and_, case, cast, func, or_, select

from app.models.asset import Asset
from app.models.generation import Generation
from app.models.generation import GenerationJob as Job
from app.models.shot import Shot
from app.models.storage import Blob
from app.models.timeline import Timeline
from app.modules.generation import schemas, service

ACTIVE = ("pending", "submitted", "running")


def base_query(project_id):
    title = case(
        (Job.target_type == "shot", func.coalesce(func.nullif(Shot.title, ""), Shot.code)),
        (Job.target_type == "asset", Asset.name),
        (Job.target_type == "timeline", Timeline.name),
        else_=None,
    )
    available = case(
        (Job.target_type == "shot", and_(Shot.id.is_not(None), Shot.deleted_at.is_(None))),
        (Job.target_type == "asset", and_(Asset.id.is_not(None), Asset.deleted_at.is_(None))),
        (
            Job.target_type == "timeline",
            and_(Timeline.id.is_not(None), Timeline.deleted_at.is_(None)),
        ),
        else_=False,
    )
    result_count = (
        select(func.count(Generation.id))
        .where(
            Generation.job_id == Job.id,
            Generation.project_id == project_id,
            Generation.output_blob_hash.is_not(None),
        )
        .correlate(Job)
        .scalar_subquery()
    )
    query = select(
        Job, title.label("target_name"), available.label("target_available"), result_count
    )
    for model, kind in ((Shot, "shot"), (Asset, "asset"), (Timeline, "timeline")):
        query = query.outerjoin(
            model,
            and_(
                Job.target_type == kind, Job.target_id == model.id, model.project_id == project_id
            ),
        )
    return query.where(Job.project_id == project_id), title


def summary(row):
    job, title, available, count = row
    raw = job.cost_raw or {}
    events = raw.get("events") or []
    return {
        "id": job.id,
        "target_type": job.target_type,
        "target_id": job.target_id,
        "target_name": title,
        "target_available": bool(available),
        "status": job.status,
        "provider": job.provider,
        "request_type": job.request_type,
        "operation": job.input_snapshot.get("operation", "generate"),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "error": job.error,
        "has_warnings": bool(raw.get("warnings")),
        "result_count": count,
        "last_event": events[-1]["message"] if events else None,
    }


def catalog(db, project_id, status="", operation="", search="", offset=0, limit=24):
    query, title = base_query(project_id)
    if operation:
        query = query.where(
            func.coalesce(Job.input_snapshot["operation"].astext, "generate") == operation
        )
    if search.strip():
        term = search.strip()
        query = query.where(
            or_(
                cast(Job.id, String).icontains(term, autoescape=True),
                Job.provider.icontains(term, autoescape=True),
                title.icontains(term, autoescape=True),
            )
        )
    # Facets use the same type/search scope and count all rows, not just the current page.
    facet = query.with_only_columns(Job.id, Job.status).subquery()
    counts = dict(db.execute(select(facet.c.status, func.count()).group_by(facet.c.status)).all())
    if status == "active":
        query = query.where(Job.status.in_(ACTIVE))
    elif status:
        query = query.where(Job.status == status)
    total = (
        sum(counts.get(s, 0) for s in ACTIVE)
        if status == "active"
        else (counts.get(status, 0) if status else sum(counts.values()))
    )
    rows = db.execute(
        query.order_by(Job.created_at.desc(), Job.id.desc()).offset(offset).limit(limit + 1)
    ).all()
    return {
        "items": [summary(row) for row in rows[:limit]],
        "total": total,
        "counts": counts,
        "has_more": len(rows) > limit,
    }


def workspace(db, ctx, job_id):
    from app.core.permissions import can

    project_id = ctx.project.id
    job = service.get_job(db, project_id, job_id)
    query, _ = base_query(project_id)
    item = summary(db.execute(query.where(Job.id == job_id)).one())
    outputs = db.execute(
        select(Generation, Blob.mime)
        .join(Blob, Blob.hash == Generation.output_blob_hash)
        .where(
            Generation.job_id == job_id,
            Generation.project_id == project_id,
            Generation.output_blob_hash.is_not(None),
        )
        .order_by(Generation.created_at, Generation.id)
        .limit(100)
    ).all()
    retries = db.execute(
        query.where(Job.input_snapshot["retry_of"].astext == str(job_id))
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(21)
    ).all()
    # The retry parent is resolved in the current project; never link to an unverified ID.
    parent = None
    if job.input_snapshot.get("retry_of"):
        row = db.execute(
            query.where(cast(Job.id, String) == str(job.input_snapshot["retry_of"]))
        ).first()
        parent = summary(row) if row else None
    permitted = can(ctx.role, "generation.trigger") and (
        job.target_type != "timeline" or can(ctx.role, "timeline.edit")
    )
    retry_exists = db.scalar(
        select(Job.id)
        .where(
            Job.project_id == project_id,
            Job.input_snapshot["retry_of"].astext == str(job_id),
            Job.status.in_((*ACTIVE, "succeeded")),
        )
        .limit(1)
    )
    return {
        "summary": item,
        "job": schemas.JobOut.model_validate(job),
        "can_cancel": permitted and job.status in ACTIVE,
        "can_retry": permitted
        and item["target_available"]
        and job.status in ("failed", "canceled")
        and not retry_exists,
        "outputs": [
            {**schemas.GenerationOut.model_validate(g).model_dump(mode="json"), "output_mime": mime}
            for g, mime in outputs
        ],
        "retry_parent": parent,
        "retries": [summary(r) for r in retries[:20]],
        "more_retries": len(retries) > 20,
    }
