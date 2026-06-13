"""assist:贯穿式 AI 对话助手会话。

一个会话绑定一个目标对象(target_type + target_id),messages 以 JSONB 数组承载
多轮对话与每条助手消息的「修改提议(ops)」。应用提议时走版本内核做快照,可回退。
"""
import uuid

from sqlalchemy import Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class AssistChat(Base, TimestampMixin):
    __tablename__ = "assist_chat"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    # 消息数组:[{id, role, content, proposal?, applied_at?, created_at}]
    messages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (Index("ix_assist_chat_target", "target_type", "target_id"),)
