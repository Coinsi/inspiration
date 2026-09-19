"""timeline DTO。"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    revision: int | None = Field(default=None, ge=0)


class SubtitleSource(BaseModel):
    kind: Literal["reviewed_transcription"]
    version_id: uuid.UUID
    annotation_id: uuid.UUID
    generation_id: uuid.UUID
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)


class SubtitleCue(BaseModel):
    start_ms: int = Field(ge=0, le=1_800_000)
    end_ms: int = Field(ge=100, le=1_800_000)
    text: str = Field(min_length=1, max_length=4000)
    source: SubtitleSource | None = None


class AudioIn(BaseModel):
    generation_id: uuid.UUID
    name: str = Field(default="音轨", max_length=255)
    kind: Literal["dialogue", "music", "effect"] = "music"
    start_ms: int = Field(default=0, ge=0, le=1_800_000)
    in_point_ms: int = Field(default=0, ge=0, le=1_800_000)
    duration_ms: int = Field(ge=100, le=1_800_000)
    gain_db: float = Field(default=0, ge=-60, le=12, allow_inf_nan=False)
    fade_in_ms: int = Field(default=0, ge=0, le=60_000)
    fade_out_ms: int = Field(default=0, ge=0, le=60_000)
    muted: bool = False


class VisualIn(BaseModel):
    id: uuid.UUID
    generation_id: uuid.UUID
    name: str = Field(default="叠加画面", max_length=255)
    track: int = Field(default=1, ge=1, le=3)
    start_ms: int = Field(default=0, ge=0, le=1_800_000)
    in_point_ms: int = Field(default=0, ge=0, le=1_800_000)
    duration_ms: int = Field(ge=100, le=1_800_000)
    x: float = Field(default=0.6, ge=0, le=1, allow_inf_nan=False)
    y: float = Field(default=0.6, ge=0, le=1, allow_inf_nan=False)
    width: float = Field(default=0.35, ge=0.05, le=1, allow_inf_nan=False)
    height: float = Field(default=0.35, ge=0.05, le=1, allow_inf_nan=False)
    opacity: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)
    fit: Literal["contain", "cover"] = "contain"
    hidden: bool = False

    @model_validator(mode="after")
    def inside_frame(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("叠加画面的位置和大小须在画框内")
        return self


class TimelineDocumentIn(BaseModel):
    revision: int = Field(ge=0)
    items: list[TimelineItemIn] = Field(default_factory=list, max_length=100)
    audio: list[AudioIn] = Field(default_factory=list, max_length=24)
    subtitles: list[SubtitleCue] = Field(default_factory=list, max_length=10000)
    visuals: list[VisualIn] = Field(default_factory=list, max_length=12)


class RestoreIn(BaseModel):
    revision: int = Field(ge=0)


class SubtitleParseIn(BaseModel):
    content: str = Field(min_length=1, max_length=1_000_000)


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
