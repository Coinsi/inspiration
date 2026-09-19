"""Project canvases, immutable editing history and durable execution batches."""

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class Canvas(Base, TimestampMixin):
    __tablename__ = "canvas"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    name: Mapped[str] = mapped_column(String(255))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    document: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)


class CanvasRevision(Base, TimestampMixin):
    __tablename__ = "canvas_revision"
    id: Mapped[uuid.UUID] = uuid_pk()
    canvas_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("canvas.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)
    __table_args__ = (Index("uq_canvas_revision", "canvas_id", "revision", unique=True),)


class CanvasRun(Base, TimestampMixin):
    __tablename__ = "canvas_run"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    canvas_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("canvas.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    request_key: Mapped[uuid.UUID] = mapped_column(UUID)
    status: Mapped[str] = mapped_column(String(24), default="running")
    snapshot: Mapped[dict] = mapped_column(JSONB)
    steps: Mapped[dict] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)
    __table_args__ = (Index("uq_canvas_run_request", "canvas_id", "request_key", unique=True),)
