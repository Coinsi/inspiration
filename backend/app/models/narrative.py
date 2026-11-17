"""narrative:原著→章节、大纲、剧本、场次。可版本化实体持 current_version_id。"""

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CodedMixin,
    ProjectScopedMixin,
    SoftDeleteMixin,
    TimestampMixin,
    uuid_pk,
)
from app.models.enums import VersionStatus


class Novel(Base, CodedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "novel"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    cover_blob_hash: Mapped[str | None] = mapped_column(String(64), ForeignKey("blob.hash"))
    source_format: Mapped[str | None] = mapped_column(String(32))
    original_blob_hash: Mapped[str | None] = mapped_column(String(128), ForeignKey("blob.hash"))
    status: Mapped[VersionStatus] = mapped_column(String(16), default=VersionStatus.draft)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Chapter(Base, TimestampMixin):
    __tablename__ = "chapter"

    id: Mapped[uuid.UUID] = uuid_pk()
    novel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("novel.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (Index("ix_chapter_novel_ordinal", "novel_id", "ordinal"),)


class Outline(Base, CodedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "outline"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[VersionStatus] = mapped_column(String(16), default=VersionStatus.draft)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Script(Base, CodedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "script"

    @property
    def content_revision(self):
        import hashlib
        import json

        return hashlib.sha256(
            json.dumps(self.content_blocks or [], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    cover_blob_hash: Mapped[str | None] = mapped_column(String(64), ForeignKey("blob.hash"))
    # 剧本正文:有序的 typed blocks(scene_heading/action/character/dialogue/parenthetical/transition)
    content_blocks: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # 由哪个章节改编而来(溯源,可空)
    source_chapter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapter.id")
    )
    status: Mapped[VersionStatus] = mapped_column(String(16), default=VersionStatus.draft)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Scene(Base, CodedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "scene"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    script_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    adapted_from_chapter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapter.id")
    )
    status: Mapped[VersionStatus] = mapped_column(String(16), default=VersionStatus.draft)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (Index("ix_scene_script_ordinal", "script_id", "ordinal"),)


class ScriptGenerationTask(Base, TimestampMixin):
    """Durable chapter adaptation, independent of the reader page lifetime."""
    __tablename__ = "script_generation_task"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("chapter.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("user.id"))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    title: Mapped[str | None] = mapped_column(String(255))
    strategy: Mapped[str | None] = mapped_column(String(32))
    script_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("script.id"))
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_script_task_chapter", "project_id", "chapter_id", "created_at"),)


class CreativeToolTask(Base, TimestampMixin):
    """Durable tool receipt and candidate output, scoped to a project object."""
    __tablename__ = "creative_tool_task"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    target_id: Mapped[uuid.UUID] = mapped_column(UUID)
    operation: Mapped[str] = mapped_column(String(32))
    request_key: Mapped[uuid.UUID] = mapped_column(UUID)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("user.id"))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_revision: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("uq_creative_tool_request", "project_id", "request_key", unique=True),
        Index("ix_creative_tool_target", "project_id", "operation", "target_id", "created_at"),
    )
