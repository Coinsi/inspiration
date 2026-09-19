"""Versioned blocking scenes and immutable camera reference provenance."""

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class DirectorScene(Base, TimestampMixin):
    __tablename__ = "director_scene"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    name: Mapped[str] = mapped_column(String(120))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    document: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)


class DirectorRevision(Base, TimestampMixin):
    __tablename__ = "director_revision"
    id: Mapped[uuid.UUID] = uuid_pk()
    scene_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("director_scene.id", ondelete="CASCADE")
    )
    revision: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)
    __table_args__ = (Index("uq_director_revision", "scene_id", "revision", unique=True),)


class DirectorReference(Base, TimestampMixin):
    __tablename__ = "director_reference"
    id: Mapped[uuid.UUID] = uuid_pk()
    scene_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("director_scene.id", ondelete="CASCADE")
    )
    request_key: Mapped[uuid.UUID] = mapped_column(UUID)
    fingerprint: Mapped[str] = mapped_column(String(64))
    generation_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("generation.id"))
    __table_args__ = (Index("uq_director_reference", "scene_id", "request_key", unique=True),)
