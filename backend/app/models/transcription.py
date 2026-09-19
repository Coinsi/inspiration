"""Durable, checkpointed transcription drafts; only reviewed copies enter search."""

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class TranscriptionRun(Base, TimestampMixin):
    __tablename__ = "transcription_run"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    version_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("media_version.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    model_key: Mapped[str] = mapped_column(String(255))
    language: Mapped[str | None] = mapped_column(String(8))
    total: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    raw_cues: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    cues: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    published_revision: Mapped[int | None] = mapped_column(Integer)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)
