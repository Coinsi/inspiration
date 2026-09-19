"""prompt 路由(对照 04 §3.4)。"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.modules.prompt import schemas, service

router = APIRouter(prefix="/projects/{project_id}", tags=["prompt"])
EDIT = require_action("prompt.edit")


@router.post("/prompts", response_model=schemas.PromptOut)
def create_prompt(data: schemas.PromptIn, ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function")):
    return service.create_prompt(db, ctx, data)


@router.get("/prompts", response_model=list[schemas.PromptOut])
def list_prompts(ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db, scope="function")):
    return service.list_prompts(db, ctx.project.id)


@router.patch("/prompts/{prompt_id}", response_model=schemas.PromptOut)
def update_prompt(
    prompt_id: uuid.UUID, data: schemas.PromptUpdate,
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function"),
):
    return service.update_prompt(db, ctx, prompt_id, data)


@router.post("/prompt-fragments", response_model=schemas.FragmentOut)
def create_fragment(data: schemas.FragmentIn, ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function")):
    return service.create_fragment(db, ctx, data)


@router.get("/prompt-fragments", response_model=list[schemas.FragmentOut])
def list_fragments(
    category: str | None = None,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.list_fragments(db, ctx.project.id, category)


@router.patch("/prompt-fragments/{fragment_id}", response_model=schemas.FragmentOut)
def update_fragment(fragment_id: uuid.UUID, data: schemas.FragmentUpdate, ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function")):
    return service.update_fragment(db, ctx, fragment_id, data)


@router.delete("/prompt-fragments/{fragment_id}", status_code=204)
def delete_fragment(fragment_id: uuid.UUID, expected_updated_at: datetime = Query(), ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function")):
    service.delete_fragment(db, ctx, fragment_id, expected_updated_at)
