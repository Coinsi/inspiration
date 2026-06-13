"""shot DTO。"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ProductionStatus, RefMode


class ShotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    scene_id: uuid.UUID
    ordinal: int
    title: str | None
    description: str
    production_status: ProductionStatus
    prompt_override: str | None
    selected_generation_id: uuid.UUID | None
    created_at: datetime | None = None
    # 关联上下文(镜头 → 场次 → 章节 → 小说),单镜头查询时为空,看板列表会填充
    scene_code: str | None = None
    scene_title: str | None = None
    chapter_id: uuid.UUID | None = None
    chapter_ordinal: int | None = None
    chapter_title: str | None = None
    novel_id: uuid.UUID | None = None
    novel_title: str | None = None
    storyboard: dict = Field(default_factory=dict)


class ShotUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    prompt_override: str | None = None
    storyboard: dict | None = None  # 传入则按 key 合并到现有分镜规格


class ShotCreate(BaseModel):
    title: str | None = None
    description: str = ""


class ReorderIn(BaseModel):
    shot_ids: list[uuid.UUID] = Field(default_factory=list)


class AssetRefIn(BaseModel):
    asset_id: uuid.UUID
    role: str = "character"
    ref_mode: RefMode = RefMode.floating
    pinned_version_id: uuid.UUID | None = None


class SetAssetRefsIn(BaseModel):
    refs: list[AssetRefIn] = Field(default_factory=list)


class AssetRefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    asset_id: uuid.UUID
    role: str
    ref_mode: RefMode
    pinned_version_id: uuid.UUID | None
    ordinal: int


class TransitionIn(BaseModel):
    to: ProductionStatus


class ComposeOut(BaseModel):
    final_prompt: str
    parts: list[str]
    overridden: bool


class BoardOut(BaseModel):
    total: int
    by_status: dict[str, int]
