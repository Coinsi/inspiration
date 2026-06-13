"""versioning 内核:version 快照、relation 边、baseline。多态表,不设库级外键。"""
import uuid

from sqlalchemy import Integer, String, UniqueConstraint, Boolean, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk
from app.models.enums import RelationType, RefMode, VersionStatus


class Version(Base, TimestampMixin):
    """不可变内容快照(多态:entity_type + entity_id)。"""

    __tablename__ = "version"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[VersionStatus] = mapped_column(String(16), default=VersionStatus.draft)
    content: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "version_no", name="uq_version_no"),
        Index("ix_version_entity", "entity_type", "entity_id"),
    )


class Relation(Base, TimestampMixin):
    """依赖图边(多态),承载溯源与影响分析。"""

    __tablename__ = "relation"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    src_type: Mapped[str] = mapped_column(String(48), nullable=False)
    src_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dst_type: Mapped[str] = mapped_column(String(48), nullable=False)
    dst_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rel_type: Mapped[RelationType] = mapped_column(String(16), nullable=False)
    ref_mode: Mapped[RefMode | None] = mapped_column(String(16))
    pinned_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ordinal: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

    __table_args__ = (
        Index("ix_relation_src", "src_type", "src_id"),
        Index("ix_relation_dst", "dst_type", "dst_id"),
    )


class Baseline(Base, TimestampMixin):
    __tablename__ = "baseline"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="custom")
    note: Mapped[str | None] = mapped_column(String(512))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class BaselineItem(Base):
    __tablename__ = "baseline_item"

    id: Mapped[uuid.UUID] = uuid_pk()
    baseline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("baseline.id", ondelete="CASCADE")
    )
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("baseline_id", "entity_type", "entity_id", name="uq_baseline_item"),
    )
