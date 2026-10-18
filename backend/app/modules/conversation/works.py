"""Standalone project works with an optional existing script association."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.core.errors import Conflict, NotFound
from app.models.identity import Project
from app.models.narrative import Script
from app.models.work import CreativeWork

router = APIRouter(prefix="/projects/{project_id}/works", tags=["works"])
DB = Depends(get_db, scope="function")


class Create(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    script_id: uuid.UUID | None = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v):
        if not v.strip():
            raise ValueError("请填写作品名称")
        return v.strip()


def get(db, project_id, work_id):
    work = db.get(CreativeWork, work_id)
    if not work or work.project_id != project_id:
        raise NotFound("作品不存在或不属于当前项目")
    # The script is an optional association, not the owner of the work or its revisions.
    return work


@router.get("")
def listing(ctx: ProjectContext = Depends(get_project_context), db=DB):
    return list(
        db.scalars(
            select(CreativeWork)
            .where(CreativeWork.project_id == ctx.project.id)
            .order_by(CreativeWork.created_at.desc())
        )
    )


@router.post("")
def create(data: Create, ctx: ProjectContext = Depends(require_action("narrative.edit")), db=DB):
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    if data.script_id:
        script = db.get(Script, data.script_id)
        if not script or script.project_id != ctx.project.id or script.deleted_at:
            raise NotFound("剧本不存在")
        if db.scalar(select(CreativeWork).where(CreativeWork.script_id == script.id)):
            raise Conflict("这份剧本已经属于一个作品，请直接选择该作品")
    else:
        script = None
    work = CreativeWork(
        project_id=ctx.project.id,
        title=data.title,
        script_id=script.id if script else None,
        created_by=ctx.user.id,
    )
    db.add(work)
    db.flush()
    from app.modules.engineering.documents import initialize

    initialize(db, ctx, work.id)
    return work


class TimelineLink(BaseModel):
    timeline_id: uuid.UUID | None = None


def timeline_ids(db, work_id):
    from app.models.engineering import EngineeringChange, EngineeringCommit
    from app.models.work import WorkTimeline

    ids = set(db.scalars(select(WorkTimeline.timeline_id).where(WorkTimeline.work_id == work_id)))
    # Historical candidates/captures already record an explicit user association.
    for snapshot in db.scalars(
        select(EngineeringCommit.snapshot).where(EngineeringCommit.work_id == work_id)
    ):
        ids.update(uuid.UUID(key) for key in snapshot.get("timeline_captures", {}))
    for change in db.scalars(select(EngineeringChange).where(EngineeringChange.work_id == work_id)):
        ids.update(uuid.UUID(key) for key in change.snapshot.get("timeline_captures", {}))
        if change.timeline_update.get("timeline_id"):
            ids.add(uuid.UUID(change.timeline_update["timeline_id"]))
    return ids


@router.get("/timeline-links")
def linked_works(timeline_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db=DB):
    from app.modules.timeline.service import _get_timeline

    _get_timeline(db, ctx.project.id, timeline_id)
    return [w for w in listing(ctx, db) if timeline_id in timeline_ids(db, w.id)]


@router.get("/{work_id}/timelines")
def work_timelines(work_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db=DB):
    from app.models.timeline import Timeline

    get(db, ctx.project.id, work_id)
    return list(
        db.scalars(
            select(Timeline)
            .where(
                Timeline.id.in_(timeline_ids(db, work_id)),
                Timeline.project_id == ctx.project.id,
                Timeline.deleted_at.is_(None),
            )
            .order_by(Timeline.created_at.desc())
        )
    )


@router.post("/{work_id}/timelines")
def link_timeline(
    work_id: uuid.UUID,
    data: TimelineLink,
    ctx: ProjectContext = Depends(require_action("timeline.edit")),
    db=DB,
):
    from app.models.timeline import Timeline
    from app.models.work import WorkTimeline
    from app.modules.timeline.service import _get_timeline

    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    work = get(db, ctx.project.id, work_id)
    if data.timeline_id:
        timeline = _get_timeline(db, ctx.project.id, data.timeline_id)
    else:
        # Repeated start clicks reuse this work's existing editing entry.
        existing = work_timelines(work_id, ctx, db)
        if existing:
            from app.modules.engineering.documents import sync_working

            sync_working(db, ctx, work_id, existing[0].id)
            return existing[0]
        timeline = Timeline(
            project_id=ctx.project.id, name=work.title, kind="main", created_by=ctx.user.id
        )
        db.add(timeline)
        db.flush()
    if not db.get(WorkTimeline, (work_id, timeline.id)):
        db.add(WorkTimeline(work_id=work_id, timeline_id=timeline.id))
        db.flush()
    from app.modules.engineering.documents import sync_working

    sync_working(db, ctx, work_id, timeline.id)
    return timeline
