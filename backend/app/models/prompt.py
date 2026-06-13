"""prompt:提示词资产 + 可复用片段库。"""
import uuid

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CodedMixin, ProjectScopedMixin, SoftDeleteMixin, TimestampMixin, uuid_pk
from app.models.enums import VersionStatus


class Prompt(Base, CodedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "prompt"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    positive: Mapped[str] = mapped_column(Text, default="")
    negative: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[VersionStatus] = mapped_column(String(16), default=VersionStatus.draft)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class PromptFragment(Base, CodedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "prompt_fragment"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    category: Mapped[str] = mapped_column(String(32), default="custom")  # quality/camera/lighting/style
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    text: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
