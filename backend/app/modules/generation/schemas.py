"""generation DTO。"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import JobStatus, RequestType


class ProviderConfigIn(BaseModel):
    provider_name: str
    kind: str = "generation"
    enabled: bool = True
    endpoint: str | None = None
    token: str | None = None  # 写入即加密
    config: dict = Field(default_factory=dict)
    capabilities: dict = Field(default_factory=dict)


class ProviderConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    provider_name: str
    kind: str
    enabled: bool
    endpoint: str | None
    config: dict
    capabilities: dict
    # 注意:不返回 token


class QuotaIn(BaseModel):
    scope: str = "project"
    user_id: uuid.UUID | None = None
    limit_cost: float = 0
    period: str = "total"


class QuotaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    scope: str
    user_id: uuid.UUID | None
    limit_cost: float
    used_cost: float
    period: str


class GenerateIn(BaseModel):
    provider: str = "mock"
    request_type: RequestType = RequestType.image
    params: dict = Field(default_factory=dict)
    provider_params: dict = Field(default_factory=dict)
    count: int = Field(default=1, ge=1, le=8)
    prompt_override: str | None = None  # 不填则用 BOM 组合
    use_references: bool = True  # 资产生图时是否把资产参考图喂给供应商(图生图/参考图)
    source_generation_id: uuid.UUID | None = None
    first_frame_id: uuid.UUID | None = None
    last_frame_id: uuid.UUID | None = None


class EstimateOut(BaseModel):
    points: float
    detail: dict


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    provider: str
    request_type: RequestType
    status: JobStatus
    estimated_cost: float | None
    actual_cost: float | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    input_snapshot: dict = Field(default_factory=dict)
    cost_raw: dict | None = None


class RefineIn(BaseModel):
    crop: tuple[float, float, float, float] = (0, 0, 1, 1)
    rotate: Literal[0, 90, 180, 270] = 0
    scale: float = Field(default=1, ge=0.25, le=4)
    brightness: float = Field(default=1, ge=0.1, le=2)
    contrast: float = Field(default=1, ge=0.1, le=2)


Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class MaskStroke(BaseModel):
    points: list[tuple[Unit, Unit]] = Field(min_length=1, max_length=2000)
    radius: float = Field(ge=0.001, le=0.25, allow_inf_nan=False)
    erase: bool = False


class InpaintIn(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=8000)
    strokes: list[MaskStroke] = Field(min_length=1, max_length=100)


class OptimizePromptIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    mode: Literal["expand", "refine", "style"] = "refine"
    media_type: Literal["image", "video"] = "image"
    style: str = Field(default="", max_length=1000)


class OptimizedPrompt(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    avoid: str = Field(default="", max_length=4000)
    explanation: str = Field(default="", max_length=4000)
    assumptions: list[Annotated[str, Field(max_length=1000)]] = Field(
        default_factory=list, max_length=20
    )


class RenderIn(BaseModel):
    height: Literal[720, 1080] = 720
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    mute: bool = False


class GenerationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    job_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    provider: str
    output_type: RequestType
    output_blob_hash: str | None
    thumbnail_hash: str | None
    prompt_snapshot: str
    rating: int | None
    is_favorite: bool
    is_selected: bool
    cost_points: float
    input_refs: dict | None = None  # 含发送给供应商的参考图溯源
    created_at: datetime


class VideoToolIn(BaseModel):
    operation: Literal["frames", "audio", "trim"]
    times_ms: list[Annotated[int, Field(ge=0, le=1_800_000)]] = Field(
        default_factory=list, max_length=12
    )
    start_ms: int = Field(default=0, ge=0, le=1_800_000)
    end_ms: int | None = Field(default=None, ge=1, le=1_800_000)


class RatePatch(BaseModel):
    rating: int | None = None
    is_favorite: bool | None = None
