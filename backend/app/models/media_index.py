"""Versioned, owned video indices and human evidence annotations."""

import uuid

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class MediaIndex(Base, TimestampMixin):
    __tablename__ = "media_index"
    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_version.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    model_key: Mapped[str] = mapped_column(String(255))
    total: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)


class MediaSegment(Base, TimestampMixin):
    __tablename__ = "media_segment"
    id: Mapped[uuid.UUID] = uuid_pk()
    index_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_index.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    frames: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float))
    __table_args__ = (UniqueConstraint("index_id", "ordinal", name="uq_media_segment_ordinal"),)


class MediaAnnotation(Base, TimestampMixin):
    __tablename__ = "media_annotation"
    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_version.id"), index=True)
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16), default="visual")
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
