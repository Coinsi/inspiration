"""Application-owned production state; no model session is the source of truth."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class ProductionRun(Base, TimestampMixin):
    __tablename__ = "production_run"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    created_by: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    request_key: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    work_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    goal: Mapped[str] = mapped_column(Text)
    engine: Mapped[str] = mapped_column(String(64), default="cloud_llm")
    model_provider: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    policy: Mapped[dict] = mapped_column(JSONB, default=dict)
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
    plan: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        Index("uq_production_request", "project_id", "created_by", "request_key", unique=True),
    )


class ProductionTask(Base, TimestampMixin):
    __tablename__ = "production_task"
    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("production_run.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    key: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    role: Mapped[str] = mapped_column(String(40), default="coordinator")
    kind: Mapped[str] = mapped_column(String(24), default="agent")
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    dependencies: Mapped[list] = mapped_column(JSONB, default=list)
    specification: Mapped[dict] = mapped_column(JSONB, default=dict)
    state: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[dict] = mapped_column(JSONB, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("uq_production_task", "run_id", "revision", "key", unique=True),)


class ProductionAttempt(Base, TimestampMixin):
    __tablename__ = "production_attempt"
    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("production_task.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    lease_token: Mapped[uuid.UUID] = mapped_column(UUID)
    status: Mapped[str] = mapped_column(String(32), default="started")
    operation: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (Index("uq_production_attempt", "task_id", "number", unique=True),)


class ProductionEvent(Base, TimestampMixin):
    __tablename__ = "production_event"
    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("production_run.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    request_key: Mapped[uuid.UUID | None] = mapped_column(UUID)
    kind: Mapped[str] = mapped_column(String(48))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        Index("uq_production_event_order", "run_id", "sequence", unique=True),
        Index("uq_production_event_request", "run_id", "request_key", unique=True),
    )
