"""Owner-scoped conversation files; CAS bytes are immutable and independently reusable."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class Attachment(Base, TimestampMixin):
    __tablename__ = "conversation_attachment"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    created_by: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    request_key: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    metadata_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    chunk_hashes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="uploading", nullable=False)
    error: Mapped[str | None] = mapped_column(String(500))
    blob_hash: Mapped[str | None] = mapped_column(String(128), ForeignKey("blob.hash"))
    thumbnail_hash: Mapped[str | None] = mapped_column(String(128), ForeignKey("blob.hash"))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID, ForeignKey("conversation.id", ondelete="SET NULL")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID, ForeignKey("conversation_message.id", ondelete="SET NULL")
    )
    __table_args__ = (
        Index("uq_attachment_request", "project_id", "created_by", "request_key", unique=True),
        Index("ix_attachment_owner_saved", "project_id", "created_by", "saved_at"),
    )
