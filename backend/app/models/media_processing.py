"""Durable provider runs; the library remains the owner of media and permissions."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class MediaProcessingRun(Base, TimestampMixin):
    __tablename__ = "media_processing_run"

    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_version.id"), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"))
    provider: Mapped[str] = mapped_column(String(16))
    operation: Mapped[str] = mapped_column(String(16), default="process")
    capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list)
    source_hash: Mapped[str] = mapped_column(ForeignKey("blob.hash"))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    remote_id: Mapped[str | None] = mapped_column(String(255))
    progress: Mapped[dict] = mapped_column(JSONB, default=dict)
    outputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("provider IN ('wise', 'mediacms')", name="ck_media_processing_provider"),
        CheckConstraint("operation IN ('process', 'delete')", name="ck_media_processing_operation"),
        CheckConstraint(
            "status IN ('queued', 'processing', 'waiting', 'ready', 'failed', 'canceled')",
            name="ck_media_processing_status",
        ),
        Index(
            "uq_media_processing_active",
            "version_id",
            "provider",
            unique=True,
            postgresql_where=text("status IN ('queued', 'processing', 'waiting')"),
        ),
    )
