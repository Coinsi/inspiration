"""review DTO。"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AnnotationKind, ReviewStatus


class ReviewIn(BaseModel):
    target_type: str = "shot"  # shot/generation
    target_id: uuid.UUID
    generation_id: uuid.UUID | None = None


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    generation_id: uuid.UUID | None
    status: ReviewStatus
    round_no: int
    decision: str | None
    created_at: datetime
    resolved_at: datetime | None


class AnnotationIn(BaseModel):
    kind: AnnotationKind = AnnotationKind.comment
    geometry: dict | None = None  # 画面框选坐标
    timecode_ms: int | None = None  # 视频批注
    comment: str = ""


class AnnotationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: AnnotationKind
    geometry: dict | None
    timecode_ms: int | None
    comment: str
    created_at: datetime


class DecideIn(BaseModel):
    approve: bool
    note: str = ""


class RevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    shot_id: uuid.UUID
    round_no: int
    note: str
    created_at: datetime
