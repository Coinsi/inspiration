"""A durable list of independent frozen shot jobs, also usable after leaving the page."""

import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.core.errors import AppError, Conflict, Forbidden, NotFound
from app.core.permissions import require
from app.models.generation import GenerationJob
from app.models.identity import Membership, Project, User
from app.models.narrative import Scene
from app.models.shot import Shot, ShotBatch, ShotBatchItem
from app.modules.generation import jobs, schemas, service
from app.modules.generation.model_routing import retry_allowed

router = APIRouter()
TRIGGER = require_action("generation.trigger")
log = logging.getLogger(__name__)


class BatchIn(BaseModel):
    request_key: uuid.UUID
    shot_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=64)
    media_type: Literal["image", "video"] = "image"


class RetryIn(BaseModel):
    job_id: uuid.UUID | None


def recovery(job):
    if job.status != "failed":
        return None
    state = job.routing_state or {}
    kind = (state.get("failure") or {}).get("kind")
    if kind in {
        "content_moderation",
        "input_moderation",
        "output_moderation",
        "invalid_parameters",
        "provider_configuration",
    }:
        return "edit"
    if not retry_allowed(job):
        return "verify"
    if state.get("phase") in {"accepted", "generated"} or state.get("resume"):
        return "receive"
    if job.external_job_id and state.get("phase") != "upstream_failed":
        return "verify"
    return "retry"


def scheduled_retry(item, job):
    return (
        job
        and job.status == "failed"
        and item.state == "watch"
        and item.attempts == 1
        and (job.routing_state or {}).get("phase") in {"rejected", "upstream_failed"}
        and (job.routing_state or {}).get("failure", {}).get("kind") == "provider_capacity"
        and not (job.input_snapshot or {}).get("retry_of")
        and recovery(job) == "retry"
    )


def describe(db, batch):
    rows = db.execute(
        select(ShotBatchItem, Shot, GenerationJob)
        .join(Shot, Shot.id == ShotBatchItem.shot_id)
        .outerjoin(GenerationJob, GenerationJob.id == ShotBatchItem.job_id)
        .where(ShotBatchItem.batch_id == batch.id)
        .order_by(Shot.ordinal)
    )
    items = []
    for item, shot, job in rows:
        failed = item.state == "failed"
        items.append(
            {
                "id": item.id,
                "shot_id": shot.id,
                "title": shot.title or shot.code,
                "job_id": item.job_id,
                "status": "failed" if failed else job.status,
                "error": item.error or (job.error if job else None),
                "action": recovery(job) if job else (None if shot.deleted_at else "prepare"),
                "auto_retry_pending": bool(scheduled_retry(item, job)),
                "attempts": item.attempts,
            }
        )
    return {"id": batch.id, "scene_id": batch.scene_id, **batch.options, "items": items}


def prepare(db, ctx, batch, item):
    try:
        with db.begin_nested():
            data = schemas.GenerateIn(
                provider=batch.options["provider"],
                request_type=batch.options["media_type"],
                count=1,
            )
            job = service.submit(
                db, ctx, "shot", item.shot_id, data, defer_dispatch=True, reuse_shot=True
            )
            item.job_id, item.state, item.error = (
                job.id,
                "dispatch" if job.status == "pending" else "watch",
                None,
            )
            item.attempts = 1
    except Exception as exc:
        if not isinstance(exc, AppError):
            log.exception("Could not prepare shot batch item %s", item.id)
        item.state = "failed"
        item.error = exc.message if isinstance(exc, AppError) else "镜头输入准备失败，请检查后重试"


@router.post("/scenes/{scene_id}/generation-batches", status_code=202)
def create(
    scene_id: uuid.UUID,
    data: BatchIn,
    ctx: ProjectContext = Depends(TRIGGER),
    db=Depends(get_db, scope="function"),
):
    db.scalar(select(Project.id).where(Project.id == ctx.project.id).with_for_update())
    scene = db.get(Scene, scene_id)
    if not scene or scene.project_id != ctx.project.id or scene.deleted_at:
        raise NotFound("场景不存在")
    ids = list(dict.fromkeys(data.shot_ids))
    options = {"media_type": data.media_type, "provider": data.provider}
    previous = db.scalar(
        select(ShotBatch).where(
            ShotBatch.project_id == ctx.project.id,
            ShotBatch.created_by == ctx.user.id,
            ShotBatch.request_key == data.request_key,
        )
    )
    if previous:
        result = describe(db, previous)
        if (
            previous.scene_id != scene_id
            or options != previous.options
            or set(ids) != {i["shot_id"] for i in result["items"]}
        ):
            raise Conflict("本次提交标识已用于另一组镜头")
        return result
    valid = set(
        db.scalars(
            select(Shot.id).where(
                Shot.id.in_(ids),
                Shot.project_id == ctx.project.id,
                Shot.scene_id == scene_id,
            )
        )
    )
    if valid != set(ids):
        raise NotFound("所选镜头已变化，请刷新列表")
    batch = ShotBatch(
        project_id=ctx.project.id,
        scene_id=scene_id,
        created_by=ctx.user.id,
        request_key=data.request_key,
        options=options,
    )
    db.add(batch)
    db.flush()
    items = []
    for sid in ids:
        item = ShotBatchItem(batch_id=batch.id, shot_id=sid)
        db.add(item)
        db.flush()
        prepare(db, ctx, batch, item)
        items.append(item)
    db.commit()
    if settings.celery_eager:
        for item in items:
            if item.state == "dispatch":
                dispatch_item(db, item)
    return describe(db, batch)


@router.get("/scenes/{scene_id}/generation-batches")
def batches(
    scene_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db=Depends(get_db, scope="function"),
):
    rows = db.scalars(
        select(ShotBatch)
        .where(
            ShotBatch.project_id == ctx.project.id,
            ShotBatch.scene_id == scene_id,
            ShotBatch.created_by == ctx.user.id,
        )
        .order_by(ShotBatch.created_at.desc())
        .limit(10)
    )
    return [describe(db, batch) for batch in rows]


def retry_one(db, ctx, item, job):
    # Other batches may follow the same job; the project lock prevents retry branches.
    db.scalar(select(Project.id).where(Project.id == ctx.project.id).with_for_update())
    latest = db.scalar(
        select(GenerationJob)
        .where(GenerationJob.shot_input_key == job.shot_input_key)
        .order_by(GenerationJob.created_at.desc(), GenerationJob.id)
        .limit(1)
    )
    if latest.id == job.id:
        latest = jobs.retry(db, ctx, job.id, defer_dispatch=True)
    item.job_id, item.state = latest.id, "dispatch" if latest.status == "pending" else "watch"
    item.attempts += 1
    item.error = None


@router.post("/shot-batch-items/{item_id}/retry")
def retry_item(
    item_id: uuid.UUID,
    data: RetryIn,
    ctx: ProjectContext = Depends(TRIGGER),
    db=Depends(get_db, scope="function"),
):
    row = db.execute(
        select(ShotBatchItem, ShotBatch)
        .join(ShotBatch)
        .where(
            ShotBatchItem.id == item_id,
            ShotBatch.project_id == ctx.project.id,
            ShotBatch.created_by == ctx.user.id,
        )
        .with_for_update(of=ShotBatchItem)
    ).first()
    if not row:
        raise NotFound("镜头任务不存在")
    item, batch = row
    if item.job_id == data.job_id:
        if item.job_id is None:
            prepare(db, ctx, batch, item)
        else:
            job = db.get(GenerationJob, item.job_id)
            if recovery(job) not in {"retry", "receive"}:
                raise Conflict("请根据失败原因调整输入或核对原任务")
            retry_one(db, ctx, item, job)
        db.commit()
        if settings.celery_eager and item.state == "dispatch":
            dispatch_item(db, item)
    return describe(db, batch)


def process(db, item):
    job = db.get(GenerationJob, item.job_id)
    batch = db.get(ShotBatch, item.batch_id)
    user, project = db.get(User, batch.created_by), db.get(Project, batch.project_id)
    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == batch.created_by, Membership.project_id == batch.project_id
        )
    )
    if not user or not user.is_active or not membership or not project or project.deleted_at:
        raise Forbidden("账号或项目权限已变化")
    ctx = ProjectContext(project, membership, user)
    require(ctx.role, "generation.trigger")
    if scheduled_retry(item, job):
        retry_one(db, ctx, item, job)
        job = db.get(GenerationJob, item.job_id)
    item.state, item.updated_at = "watch", datetime.now(UTC)
    if job.status == "pending":
        jobs.dispatch(db, job)
    else:
        db.commit()


def dispatch_item(db, item):
    try:
        process(db, item)
    except Exception as exc:
        db.rollback()
        if not isinstance(exc, AppError):
            log.exception("Could not dispatch shot batch item %s", item.id)
        item.state = "failed"
        item.error = exc.message if isinstance(exc, AppError) else "镜头任务调度失败，请检查后重试"
        job = db.scalar(
            select(GenerationJob).where(GenerationJob.id == item.job_id).with_for_update()
        )
        if job.status == "pending":
            from app.modules.generation.billing import unsuccessful

            unsuccessful(db, job)
            job.error = item.error
            jobs.event(job, "failed", item.error)
        db.commit()


def run_once(session_factory=SessionLocal):
    # Republish only pending jobs; execute() claims a job once before contacting the provider.
    cutoff = datetime.now(UTC) - timedelta(seconds=30)
    pending = and_(GenerationJob.status == "pending", ShotBatchItem.updated_at < cutoff)
    capacity = and_(
        GenerationJob.status == "failed",
        ShotBatchItem.attempts == 1,
        GenerationJob.updated_at < cutoff,
        GenerationJob.routing_state["phase"].astext.in_(("rejected", "upstream_failed")),
        GenerationJob.input_snapshot["retry_of"].astext.is_(None),
        GenerationJob.routing_state["failure"]["kind"].astext == "provider_capacity",
    )
    with session_factory() as db:
        item = db.scalar(
            select(ShotBatchItem)
            .join(GenerationJob, GenerationJob.id == ShotBatchItem.job_id)
            .where(
                or_(
                    ShotBatchItem.state == "dispatch",
                    and_(ShotBatchItem.state == "watch", or_(pending, capacity)),
                )
            )
            .order_by(ShotBatchItem.created_at, ShotBatchItem.id)
            .with_for_update(of=ShotBatchItem, skip_locked=True)
            .limit(1)
        )
        if not item:
            return False
        dispatch_item(db, item)
        return True


def start():
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                worked = run_once()
            except Exception:
                log.exception("Shot batch dispatcher unavailable")
                worked = False
            stop.wait(0.15 if worked else 2)

    thread = threading.Thread(target=loop, name="shot-batch-dispatch", daemon=True)
    thread.start()

    def shutdown():
        stop.set()
        thread.join(timeout=5)

    shutdown.is_alive = thread.is_alive
    return shutdown
