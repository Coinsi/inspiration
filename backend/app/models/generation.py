"""generation:供应商配置、配额、生成任务、不可变溯源/变体。"""
import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk
from app.models.enums import JobStatus, ProviderKind, QuotaScope, RequestType


class ProviderConfig(Base, TimestampMixin):
    """供应商配置 + 加密密钥(platform 模块)。"""

    __tablename__ = "provider_config"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[ProviderKind] = mapped_column(String(16), default=ProviderKind.generation)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    capabilities: Mapped[dict] = mapped_column(JSONB, default=dict)
    endpoint: Mapped[str | None] = mapped_column(String(512))
    credentials_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (UniqueConstraint("project_id", "provider_name", name="uq_provider"),)


class Quota(Base, TimestampMixin):
    __tablename__ = "quota"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    scope: Mapped[QuotaScope] = mapped_column(String(16), default=QuotaScope.project)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id"))
    limit_cost: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    used_cost: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    period: Mapped[str] = mapped_column(String(16), default="total")

    __table_args__ = (UniqueConstraint("project_id", "scope", "user_id", name="uq_quota"),)


class GenerationJob(Base, TimestampMixin):
    """异步生成任务(生命周期)。input_snapshot 冻结输入(溯源雏形)。"""

    __tablename__ = "generation_job"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    target_type: Mapped[str] = mapped_column(String(16), default="shot")  # shot/asset
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    request_type: Mapped[RequestType] = mapped_column(String(16), default=RequestType.image)
    status: Mapped[JobStatus] = mapped_column(String(16), default=JobStatus.pending)
    external_job_id: Mapped[str | None] = mapped_column(String(255))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    input_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    estimated_cost: Mapped[float | None] = mapped_column(Numeric(14, 2))
    actual_cost: Mapped[float | None] = mapped_column(Numeric(14, 2))
    cost_raw: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (
        Index("ix_job_project_status", "project_id", "status"),
        Index("ix_job_sched", "status", "priority"),
    )


class Generation(Base, TimestampMixin):
    """不可变溯源记录 + 变体。"""

    __tablename__ = "generation"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("generation_job.id"))
    target_type: Mapped[str] = mapped_column(String(16), default="shot")
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    seed: Mapped[int | None] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    prompt_snapshot: Mapped[str] = mapped_column(Text, default="")
    input_refs: Mapped[dict] = mapped_column(JSONB, default=dict)
    output_blob_hash: Mapped[str | None] = mapped_column(String(128), ForeignKey("blob.hash"))
    output_type: Mapped[RequestType] = mapped_column(String(16), default=RequestType.image)
    thumbnail_hash: Mapped[str | None] = mapped_column(String(128), ForeignKey("blob.hash"))
    rating: Mapped[int | None] = mapped_column(Integer)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_points: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    cost_raw: Mapped[dict | None] = mapped_column(JSONB)

    if settings.enable_pgvector:  # EXT-01:pgvector 启用时才建向量列
        from pgvector.sqlalchemy import Vector

        embedding = mapped_column(Vector(512), nullable=True)

    __table_args__ = (Index("ix_generation_target", "target_type", "target_id"),)
