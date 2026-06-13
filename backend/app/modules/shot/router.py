"""shot 路由(对照 04 §3.5)。"""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.models.enums import ProductionStatus
from app.modules.shot import schemas, service

router = APIRouter(prefix="/projects/{project_id}", tags=["shot"])
EDIT = require_action("shot.edit")


@router.get("/scenes/{scene_id}/shots", response_model=list[schemas.ShotOut])
def list_shots(scene_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    return service.list_shots(db, ctx.project.id, scene_id)


@router.post("/scenes/{scene_id}/shots", response_model=schemas.ShotOut)
def create_shot(
    scene_id: uuid.UUID, data: schemas.ShotCreate,
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db),
):
    return service.create_shot(db, ctx, scene_id, data)


@router.post("/scenes/{scene_id}/shots/reorder", response_model=list[schemas.ShotOut])
def reorder_shots(
    scene_id: uuid.UUID, data: schemas.ReorderIn,
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db),
):
    return service.reorder_shots(db, ctx, scene_id, data.shot_ids)


@router.post("/scenes/{scene_id}/breakdown", response_model=list[schemas.ShotOut])
def breakdown_scene(
    scene_id: uuid.UUID, strategy: str | None = None,
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db),
):
    return service.breakdown_scene(db, ctx, scene_id, strategy)


@router.delete("/shots/{shot_id}", status_code=204)
def delete_shot(shot_id: uuid.UUID, ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db)):
    service.delete_shot(db, ctx, shot_id)


@router.get("/shots", response_model=list[schemas.ShotOut])
def list_all_shots(
    status: ProductionStatus | None = None,
    novel_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db),
):
    return service.list_all_shots(db, ctx.project.id, status, novel_id, chapter_id)


@router.get("/shots/board", response_model=schemas.BoardOut)
def board(ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    return service.board(db, ctx.project.id)


@router.get("/shots/{shot_id}", response_model=schemas.ShotOut)
def get_shot(shot_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    return service.get(db, ctx.project.id, shot_id)


@router.patch("/shots/{shot_id}", response_model=schemas.ShotOut)
def update_shot(
    shot_id: uuid.UUID, data: schemas.ShotUpdate,
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db),
):
    return service.update(db, ctx, shot_id, data)


@router.put("/shots/{shot_id}/asset-refs", response_model=list[schemas.AssetRefOut])
def set_asset_refs(
    shot_id: uuid.UUID, data: schemas.SetAssetRefsIn,
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db),
):
    return service.set_asset_refs(db, ctx, shot_id, data.refs)


@router.get("/shots/{shot_id}/asset-refs", response_model=list[schemas.AssetRefOut])
def list_asset_refs(shot_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    return service.list_asset_refs(db, ctx.project.id, shot_id)


@router.post("/shots/{shot_id}/transition", response_model=schemas.ShotOut)
def transition(
    shot_id: uuid.UUID, data: schemas.TransitionIn,
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db),
):
    return service.transition(db, ctx, shot_id, data.to)


@router.get("/shots/{shot_id}/compose-prompt", response_model=schemas.ComposeOut)
def compose_prompt(shot_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    return service.compose_prompt(db, ctx.project.id, shot_id)
