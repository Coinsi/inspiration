"""Library visibility belongs to the application, independently of generation jobs."""

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class GeneratedMaterialState(Base, SoftDeleteMixin, TimestampMixin):
    __tablename__ = "generated_material_state"
    generation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation.id", ondelete="CASCADE"), primary_key=True
    )

    name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    metadata_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    poster_hash: Mapped[str | None] = mapped_column(ForeignKey("blob.hash"))
