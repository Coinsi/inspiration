"""Application-owned conversations; execution references are adapter details."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversation"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    created_by: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    title: Mapped[str] = mapped_column(String(120), default="新的对话")
    work_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("creative_work.id"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_conversation_owner", "project_id", "created_by", "updated_at"),)


class ConversationMessage(Base, TimestampMixin):
    __tablename__ = "conversation_message"
    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    request_key: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    execution_kind: Mapped[str | None] = mapped_column(String(40))
    execution_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
    progress: Mapped[list] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(String(1000))
    __table_args__ = (
        Index("uq_conversation_ordinal", "conversation_id", "ordinal", unique=True),
        Index("uq_conversation_request", "conversation_id", "request_key", "role", unique=True),
        Index("ix_conversation_execution", "execution_id"),
    )
