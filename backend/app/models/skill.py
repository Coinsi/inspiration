"""Versioned creative instructions; data only, never executable code."""

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class CreativeSkill(Base, TimestampMixin):
    __tablename__ = "creative_skill"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    name: Mapped[str] = mapped_column(String(120))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    document: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)


class CreativeSkillVersion(Base, TimestampMixin):
    __tablename__ = "creative_skill_version"
    id: Mapped[uuid.UUID] = uuid_pk()
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("creative_skill.id", ondelete="CASCADE")
    )
    revision: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)
    __table_args__ = (Index("uq_creative_skill_revision", "skill_id", "revision", unique=True),)


class SourceImport(Base, TimestampMixin):
    __tablename__ = "source_import"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    request_key: Mapped[uuid.UUID] = mapped_column(UUID)
    source: Mapped[str] = mapped_column(String(1000))
    fingerprint: Mapped[str] = mapped_column(String(64))
    asset_ids: Mapped[list] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)
    __table_args__ = (Index("uq_source_import", "project_id", "request_key", unique=True),)
