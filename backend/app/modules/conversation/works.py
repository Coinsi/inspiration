"""Project works and their existing scripts; independent of the execution adapter."""

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
    script = db.get(Script, work.script_id)
    if not script or script.deleted_at:
        raise NotFound("作品正文已移入回收站，请先恢复")
    return work


@router.get("")
def listing(ctx: ProjectContext = Depends(get_project_context), db=DB):
    return list(
        db.scalars(
            select(CreativeWork)
            .join(Script)
            .where(CreativeWork.project_id == ctx.project.id, Script.deleted_at.is_(None))
            .order_by(CreativeWork.created_at.desc())
        )
    )


@router.post("")
def create(data: Create, ctx: ProjectContext = Depends(require_action("narrative.edit")), db=DB):
    from app.modules.narrative.service import create_script

    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    if data.script_id:
        script = db.get(Script, data.script_id)
        if not script or script.project_id != ctx.project.id or script.deleted_at:
            raise NotFound("剧本不存在")
        if db.scalar(select(CreativeWork).where(CreativeWork.script_id == script.id)):
            raise Conflict("这份剧本已经属于一个作品，请直接选择该作品")
    else:
        script = create_script(db, ctx, data.title)
    work = CreativeWork(
        project_id=ctx.project.id, title=data.title, script_id=script.id, created_by=ctx.user.id
    )
    db.add(work)
    db.flush()
    return work
