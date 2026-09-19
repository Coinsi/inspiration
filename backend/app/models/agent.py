"""Durable, bounded project tool runs with reviewable writes."""

import uuid

from sqlalchemy import Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_run"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)
    request_key: Mapped[uuid.UUID] = mapped_column(UUID)
    goal: Mapped[str] = mapped_column(String(8000))
    engine: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    scope: Mapped[list] = mapped_column(JSONB)
    skill_snapshot: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    max_turns: Mapped[int] = mapped_column(Integer, default=12)
    turns: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[list] = mapped_column(JSONB, default=list)
    result: Mapped[str | None] = mapped_column(String(8000))
    error: Mapped[str | None] = mapped_column(String(1000))
    claim_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    __table_args__ = (
        Index("uq_agent_request", "project_id", "created_by", "request_key", unique=True),
    )
