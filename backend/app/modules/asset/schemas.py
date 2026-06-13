"""asset DTO。"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AssetType, VersionStatus


class AssetIn(BaseModel):
    type: AssetType
    name: str = Field(min_length=1, max_length=255)
    summary: str | None = None
    metadata: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class AssetUpdate(BaseModel):
    name: str | None = None
    summary: str | None = None
    metadata: dict | None = None
    tags: list[str] | None = None


class BatchDeleteIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1)


class AssetOut(BaseModel):
    id: uuid.UUID
    code: str
    type: AssetType
    name: str
    summary: str | None
    status: VersionStatus
    current_version_id: uuid.UUID | None
    representative_blob_hash: str | None
    metadata: dict
    tags: list[str]
    created_at: datetime
    ref_count: int = 0  # 参考图数(列表页角标)
    gen_count: int = 0  # 概念图(生成结果)数
    shot_count: int = 0  # 引用本资产的镜头数(图谱气泡大小/列表排序)

    @classmethod
    def of(cls, a, *, ref_count: int = 0, gen_count: int = 0, shot_count: int = 0) -> "AssetOut":
        # ORM 列属性名为 meta(映射 DB 列 metadata),此处显式转换避免别名歧义
        return cls(
            id=a.id,
            code=a.code,
            type=a.type,
            name=a.name,
            summary=a.summary,
            status=a.status,
            current_version_id=a.current_version_id,
            representative_blob_hash=a.representative_blob_hash,
            metadata=a.meta or {},
            tags=a.tags or [],
            created_at=a.created_at,
            ref_count=ref_count,
            gen_count=gen_count,
            shot_count=shot_count,
        )


class ReferenceImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    blob_hash: str
    role: str
    note: str | None
    ordinal: int


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_no: int
    label: str | None
    status: VersionStatus
    is_locked: bool
    content: dict
    created_by: uuid.UUID | None
    created_at: datetime


class CommitVersionIn(BaseModel):
    label: str | None = None


class VisualIdentityIn(BaseModel):
    strategy: str = "prompt"  # prompt/reference/lora
    prompt_fragment_id: uuid.UUID | None = None
    lora_ref: dict | None = None
    reference_set: dict | None = None
    config: dict = Field(default_factory=dict)


class VisualIdentityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    asset_id: uuid.UUID
    strategy: str
    prompt_fragment_id: uuid.UUID | None
    lora_ref: dict | None
    reference_set: dict | None
    config: dict
