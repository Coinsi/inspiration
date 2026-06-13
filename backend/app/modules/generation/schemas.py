"""generation DTO。"""
import uuid
from datetime import datetime

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


class RatePatch(BaseModel):
    rating: int | None = None
    is_favorite: bool | None = None
