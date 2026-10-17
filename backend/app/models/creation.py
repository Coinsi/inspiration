"""Standalone workbench ideas, with results stored by the shared generation pipeline."""

import uuid

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class Creation(Base, TimestampMixin):
    __tablename__ = "workbench_creation"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)
    request_key: Mapped[uuid.UUID] = mapped_column(UUID)
    title: Mapped[str] = mapped_column(String(120))
    work_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("creative_work.id"))
    request: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        Index("uq_creation_request", "project_id", "created_by", "request_key", unique=True),
    )
