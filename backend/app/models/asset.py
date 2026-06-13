"""asset:蓝图资产库 + 参考图集 + 元数据模板。embedding 预留(EXT-01)。"""
import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, CodedMixin, ProjectScopedMixin, SoftDeleteMixin, TimestampMixin, uuid_pk
from app.models.enums import AssetType, VersionStatus


class Asset(Base, CodedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "asset"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    type: Mapped[AssetType] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[VersionStatus] = mapped_column(String(16), default=VersionStatus.draft)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    representative_blob_hash: Mapped[str | None] = mapped_column(String(128), ForeignKey("blob.hash"))
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    scope: Mapped[str] = mapped_column(String(16), default="project")  # EXT-05 预留
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    if settings.enable_pgvector:  # EXT-01:pgvector 启用时才建向量列
        from pgvector.sqlalchemy import Vector

        embedding = mapped_column(Vector(512), nullable=True)

    __table_args__ = (Index("ix_asset_project_type", "project_id", "type"),)


class AssetReferenceImage(Base, TimestampMixin):
    __tablename__ = "asset_reference_image"

    id: Mapped[uuid.UUID] = uuid_pk()
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("asset.id", ondelete="CASCADE"))
    blob_hash: Mapped[str] = mapped_column(String(128), ForeignKey("blob.hash"))
    role: Mapped[str] = mapped_column(String(32), default="ref")  # ref/turnaround/expression
    note: Mapped[str | None] = mapped_column(String(255))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)


class MetadataTemplate(Base, TimestampMixin):
    """类型字段模板(EXT-10 自定义字段雏形)。"""

    __tablename__ = "metadata_template"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    target: Mapped[str] = mapped_column(String(32), default="asset")
    asset_type: Mapped[AssetType | None] = mapped_column(String(16))
    schema: Mapped[dict] = mapped_column(JSONB, default=dict)
