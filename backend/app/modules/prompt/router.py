"""prompt 路由(对照 04 §3.4)。"""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.modules.prompt import schemas, service

router = APIRouter(prefix="/projects/{project_id}", tags=["prompt"])
EDIT = require_action("prompt.edit")


@router.post("/prompts", response_model=schemas.PromptOut)
def create_prompt(data: schemas.PromptIn, ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db)):
    return service.create_prompt(db, ctx, data)


@router.get("/prompts", response_model=list[schemas.PromptOut])
def list_prompts(ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    return service.list_prompts(db, ctx.project.id)


@router.patch("/prompts/{prompt_id}", response_model=schemas.PromptOut)
def update_prompt(
    prompt_id: uuid.UUID, data: schemas.PromptUpdate,
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db),
):
    return service.update_prompt(db, ctx, prompt_id, data)


@router.post("/prompt-fragments", response_model=schemas.FragmentOut)
def create_fragment(data: schemas.FragmentIn, ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db)):
    return service.create_fragment(db, ctx, data)


@router.get("/prompt-fragments", response_model=list[schemas.FragmentOut])
def list_fragments(
    category: str | None = None,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db),
):
    return service.list_fragments(db, ctx.project.id, category)
