"""timeline DTO。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CutKind


class TimelineIn(BaseModel):
    name: str
    kind: str = "main"


class TimelineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    kind: str
    created_at: datetime


class TimelineItemIn(BaseModel):
    shot_id: uuid.UUID
    generation_id: uuid.UUID | None = None
    in_point_ms: int = Field(0, ge=0, le=1_800_000)
    out_point_ms: int = Field(0, ge=0, le=1_800_000)
    duration_ms: int = Field(0, ge=0, le=1_800_000)
    transition: dict | None = None
    note: str | None = None


class SetItemsIn(BaseModel):
    items: list[TimelineItemIn] = Field(default_factory=list, max_length=100)


class TimelineItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    shot_id: uuid.UUID
    generation_id: uuid.UUID | None
    ordinal: int
    in_point_ms: int
    out_point_ms: int
    duration_ms: int
    transition: dict | None
    note: str | None


class CutIn(BaseModel):
    name: str
    kind: CutKind = CutKind.rough
    timeline_id: uuid.UUID


class CutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    kind: CutKind
    timeline_id: uuid.UUID
    baseline_id: uuid.UUID | None
    status: str
    created_at: datetime


class BaselineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    kind: str
    note: str | None
    created_at: datetime


class BaselineItemView(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    version_id: uuid.UUID
