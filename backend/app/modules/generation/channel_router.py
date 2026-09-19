import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, require_action
from app.modules.generation import channels

router = APIRouter(prefix="/projects/{project_id}/model-channels", tags=["model-channels"])
MANAGE = require_action("provider.manage")


@router.get("")
def catalog(ctx: ProjectContext = Depends(MANAGE), db: Session = Depends(get_db, scope="function")):
    return channels.catalog(db, ctx.project.id)


@router.post("")
def create(
    data: channels.ChannelIn,
    ctx: ProjectContext = Depends(MANAGE),
    db: Session = Depends(get_db, scope="function"),
):
    row = channels.save_channel(db, ctx, data)
    return {"id": row.id}


@router.post("/import-legacy")
def import_legacy(
    ctx: ProjectContext = Depends(MANAGE), db: Session = Depends(get_db, scope="function")
):
    return channels.import_legacy(db, ctx)


@router.put("/{channel_id}")
def update(
    channel_id: uuid.UUID,
    data: channels.ChannelIn,
    ctx: ProjectContext = Depends(MANAGE),
    db: Session = Depends(get_db, scope="function"),
):
    row = channels.save_channel(db, ctx, data, channel_id)
    return {"id": row.id}


@router.post("/{channel_id}/discover")
def discover(
    channel_id: uuid.UUID,
    ctx: ProjectContext = Depends(MANAGE),
    db: Session = Depends(get_db, scope="function"),
):
    return channels.discover(db, ctx, channel_id)


@router.post("/{channel_id}/models")
def add_model(
    channel_id: uuid.UUID,
    data: channels.ModelIn,
    ctx: ProjectContext = Depends(MANAGE),
    db: Session = Depends(get_db, scope="function"),
):
    return channels.public_model(channels.save_model(db, ctx, channel_id, data))


@router.put("/{channel_id}/models/{model_id}")
def update_model(
    channel_id: uuid.UUID,
    model_id: uuid.UUID,
    data: channels.ModelIn,
    ctx: ProjectContext = Depends(MANAGE),
    db: Session = Depends(get_db, scope="function"),
):
    return channels.public_model(channels.save_model(db, ctx, channel_id, data, model_id))
