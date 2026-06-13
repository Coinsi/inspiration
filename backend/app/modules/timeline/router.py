"""timeline 路由(对照 04 §3.8 / §3.7 基线)。"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.modules.timeline import schemas, service

router = APIRouter(prefix="/projects/{project_id}", tags=["timeline"])
EDIT = require_action("timeline.edit")
LOCK = require_action("lock.baseline")


@router.post("/timelines", response_model=schemas.TimelineOut)
def create_timeline(data: schemas.TimelineIn, ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db)):
    return service.create_timeline(db, ctx, data)


@router.get("/timelines", response_model=list[schemas.TimelineOut])
def list_timelines(ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    return service.list_timelines(db, ctx.project.id)


@router.put("/timelines/{timeline_id}/items", response_model=list[schemas.TimelineItemOut])
def set_items(
    timeline_id: uuid.UUID, data: schemas.SetItemsIn,
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db),
):
    return service.set_items(db, ctx, timeline_id, data.items)


@router.get("/timelines/{timeline_id}/items", response_model=list[schemas.TimelineItemOut])
def list_items(timeline_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    return service.list_items(db, ctx.project.id, timeline_id)


@router.post("/cuts", response_model=schemas.CutOut)
def create_cut(data: schemas.CutIn, ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db)):
    return service.create_cut(db, ctx, data)


@router.post("/cuts/{cut_id}/finalize", response_model=schemas.BaselineOut)
def finalize_cut(cut_id: uuid.UUID, ctx: ProjectContext = Depends(LOCK), db: Session = Depends(get_db)):
    return service.finalize_cut(db, ctx, cut_id)


@router.get("/baselines", response_model=list[schemas.BaselineOut])
def list_baselines(ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    return service.list_baselines(db, ctx.project.id)


@router.get("/baselines/compare")
def compare_baselines(
    a: uuid.UUID = Query(...),
    b: uuid.UUID = Query(...),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db),
):
    return service.compare_baselines(db, ctx.project.id, a, b)
