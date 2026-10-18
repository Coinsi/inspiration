"""A work survives individual conversations and generation attempts."""

import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class CreativeWork(Base, TimestampMixin):
    __tablename__ = "creative_work"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    title: Mapped[str] = mapped_column(String(120))
    script_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("script.id"), unique=True)
    engineering_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)


class WorkTimeline(Base):
    """Durable navigation links, separate from adopted snapshots."""

    __tablename__ = "work_timeline"
    work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("creative_work.id"), primary_key=True)
    timeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("timeline.id"), primary_key=True)
    document_revision: Mapped[int | None] = mapped_column(Integer)
