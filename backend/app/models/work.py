"""A work survives individual conversations and generation attempts."""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class CreativeWork(Base, TimestampMixin):
    __tablename__ = "creative_work"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    title: Mapped[str] = mapped_column(String(120))
    script_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("script.id"), unique=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)
