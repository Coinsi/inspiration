"""setting 路由:故事圣经(设定库)+ 一键全书提取。"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.modules.asset import schemas as asset_schemas
from app.modules.setting import schemas, service

router = APIRouter(prefix="/projects/{project_id}", tags=["setting"])

EDIT = require_action("narrative.edit")
ASSET_EDIT = require_action("asset.edit")


@router.get("/settings", response_model=list[schemas.SettingOut])
def list_settings(
    novel_id: uuid.UUID | None = Query(None),
    category: str | None = Query(None),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.list_settings(db, ctx.project.id, novel_id, category)


@router.post("/settings", response_model=schemas.SettingOut)
def create_setting(
    data: schemas.SettingIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.create_setting(db, ctx, data)


@router.patch("/settings/{setting_id}", response_model=schemas.SettingOut)
def update_setting(
    setting_id: uuid.UUID,
    data: schemas.SettingUpdate,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.update_setting(db, ctx, setting_id, data)


@router.delete("/settings/{setting_id}", status_code=204)
def delete_setting(
    setting_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    service.delete_setting(db, ctx, setting_id)


@router.post("/settings/{setting_id}/to-asset", response_model=asset_schemas.AssetOut)
def setting_to_asset(
    setting_id: uuid.UUID,
    ctx: ProjectContext = Depends(ASSET_EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return asset_schemas.AssetOut.of(service.to_asset(db, ctx, setting_id))


# ── 一键提取(默认全书,可选章节范围) ──
@router.post("/novels/{novel_id}/extract-settings", response_model=schemas.ExtractionOut)
def start_extraction(
    novel_id: uuid.UUID,
    data: schemas.ExtractIn | None = None,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    d = data or schemas.ExtractIn()
    return service.start_extraction(db, ctx, novel_id, d.from_chapter, d.to_chapter, d.concurrency)


@router.get(
    "/novels/{novel_id}/extract-settings/latest", response_model=schemas.ExtractionOut | None
)
def latest_extraction(
    novel_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.latest_extraction(db, ctx.project.id, novel_id)


@router.get("/setting-extractions/{job_id}", response_model=schemas.ExtractionOut)
def get_extraction(
    job_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.get_extraction(db, ctx.project.id, job_id)


@router.post("/setting-extractions/{job_id}/cancel", response_model=schemas.ExtractionOut)
def cancel_extraction(
    job_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.cancel_extraction(db, ctx, job_id)
