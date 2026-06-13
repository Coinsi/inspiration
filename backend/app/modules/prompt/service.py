"""prompt 业务:提示词与片段 CRUD。"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import ProjectContext
from app.core.errors import NotFound
from app.models.prompt import Prompt, PromptFragment
from app.modules.prompt import schemas
from app.platform import coding


def create_prompt(db: Session, ctx: ProjectContext, data: schemas.PromptIn) -> Prompt:
    p = Prompt(
        project_id=ctx.project.id, code=coding.next_code(db, ctx.project, "prompt"),
        name=data.name, positive=data.positive, negative=data.negative,
        tags=data.tags, created_by=ctx.user.id,
    )
    db.add(p)
    db.flush()
    return p


def update_prompt(db: Session, ctx: ProjectContext, prompt_id: uuid.UUID, data: schemas.PromptUpdate) -> Prompt:
    p = db.get(Prompt, prompt_id)
    if p is None or p.project_id != ctx.project.id or p.deleted_at is not None:
        raise NotFound("提示词不存在")
    for f in ("name", "positive", "negative", "tags"):
        v = getattr(data, f)
        if v is not None:
            setattr(p, f, v)
    db.flush()
    return p


def list_prompts(db: Session, project_id: uuid.UUID) -> list[Prompt]:
    return list(
        db.scalars(
            select(Prompt).where(Prompt.project_id == project_id, Prompt.deleted_at.is_(None))
            .order_by(Prompt.created_at.desc())
        )
    )


def create_fragment(db: Session, ctx: ProjectContext, data: schemas.FragmentIn) -> PromptFragment:
    f = PromptFragment(
        project_id=ctx.project.id, code=coding.next_code(db, ctx.project, "prompt_fragment"),
        category=data.category, name=data.name, text=data.text, created_by=ctx.user.id,
    )
    db.add(f)
    db.flush()
    return f


def list_fragments(db: Session, project_id: uuid.UUID, category: str | None = None) -> list[PromptFragment]:
    stmt = select(PromptFragment).where(
        PromptFragment.project_id == project_id, PromptFragment.deleted_at.is_(None)
    )
    if category:
        stmt = stmt.where(PromptFragment.category == category)
    return list(db.scalars(stmt.order_by(PromptFragment.created_at.desc())))
