"""storage:内容寻址存储(CAS)。二进制按 hash 去重一份。"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base, TimestampMixin, uuid_pk


class Blob(Base, TimestampMixin):
    __tablename__ = "blob"

    hash: Mapped[str] = mapped_column(String(128), primary_key=True)  # 内容哈希(sha256)
    storage_uri: Mapped[str] = mapped_column(String(512), nullable=False)  # MinIO 对象路径
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class BlobDeletion(Base, TimestampMixin):
    """Durable operation captures an immutable physical identity, never just a hash."""

    __tablename__ = "blob_deletion"
    id: Mapped[uuid.UUID] = uuid_pk()
    blob_hash: Mapped[str] = mapped_column(String(128), index=True)
    storage_uri: Mapped[str] = mapped_column(String(512), index=True)
    status: Mapped[str] = mapped_column(String(16), default="marked", index=True)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(100))


class TimelineMediaPreview(Base):
    __tablename__ = "timeline_media_preview"

    source_hash: Mapped[str] = mapped_column(ForeignKey("blob.hash", ondelete="CASCADE"), primary_key=True)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_timeline_preview_due", "due_at", postgresql_where=text("due_at IS NOT NULL")),)


class MediaRendition(Base):
    """Disposable presentation copies, shared only through an authorized source."""

    __tablename__ = "media_rendition"

    source_hash: Mapped[str] = mapped_column(
        ForeignKey("blob.hash", ondelete="CASCADE"), primary_key=True
    )
    thumbnail_hash: Mapped[str | None] = mapped_column(ForeignKey("blob.hash"))
    display_hash: Mapped[str | None] = mapped_column(ForeignKey("blob.hash"))
    playback_hash: Mapped[str | None] = mapped_column(ForeignKey("blob.hash"))
    priority: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # NULL means the work has finished. A future time backs off a failed attempt.
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (
        Index("ix_media_rendition_due", "due_at", postgresql_where=text("due_at IS NOT NULL")),
    )
