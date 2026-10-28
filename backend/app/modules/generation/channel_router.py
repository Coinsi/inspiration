import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, require_action
from app.modules.generation import channels, presets


class ModelBatchIn(BaseModel):
    models: list[channels.ModelIn] = Field(min_length=1, max_length=100)


def create_router(prefix, manage):
    router = APIRouter(prefix=prefix, tags=["model-channels"])
    MANAGE = manage

    @router.get("")
    def catalog(
        ctx: ProjectContext = Depends(MANAGE), db: Session = Depends(get_db, scope="function")
    ):
        return channels.catalog(db, ctx.project.id)

    @router.post("")
    def create(
        data: channels.ChannelIn,
        ctx: ProjectContext = Depends(MANAGE),
        db: Session = Depends(get_db, scope="function"),
    ):
        row = channels.save_channel(db, ctx, data)
        return {"id": row.id}

    @router.post("/check")
    def check(
        data: channels.ChannelIn,
        channel_id: uuid.UUID | None = None,
        ctx: ProjectContext = Depends(MANAGE),
        db: Session = Depends(get_db, scope="function"),
    ):
        result = channels.check_connection(db, ctx, data, channel_id)
        return {
            "message": "地址与鉴权通过，模型目录可读取；尚未验证实际生成能力",
            "model_count": len(result["models"]),
        }

    @router.post("/{channel_id}/models/batch")
    def batch(
        channel_id: uuid.UUID,
        data: ModelBatchIn,
        ctx: ProjectContext = Depends(MANAGE),
        db: Session = Depends(get_db, scope="function"),
    ):
        return [
            channels.public_model(channels.save_model(db, ctx, channel_id, item))
            for item in data.models
        ]

    @router.post("/import-legacy")
    def import_legacy(
        ctx: ProjectContext = Depends(MANAGE), db: Session = Depends(get_db, scope="function")
    ):
        return channels.import_legacy(db, ctx)

    @router.get("/presets")
    def preset_catalog(ctx: ProjectContext = Depends(MANAGE)):
        return presets.catalog()

    @router.post("/connect")
    def connect(
        data: presets.ConnectIn,
        ctx: ProjectContext = Depends(MANAGE),
        db: Session = Depends(get_db, scope="function"),
    ):
        return presets.connect(db, ctx, data)

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

    @router.delete("/{channel_id}", status_code=204)
    def remove(
        channel_id: uuid.UUID,
        revision: int = Query(ge=1),
        ctx: ProjectContext = Depends(MANAGE),
        db: Session = Depends(get_db, scope="function"),
    ):
        channels.remove_channel(db, ctx, channel_id, revision)

    return router


router = create_router("/projects/{project_id}/model-channels", require_action("provider.manage"))
