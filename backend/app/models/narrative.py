"""narrative:原著→章节、大纲、剧本、场次。可版本化实体持 current_version_id。"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    legacy_blocks: Mapped[list] = mapped_column(
        "content_blocks", JSONB, nullable=False, default=list, server_default="[]"
    )
    screenplay: Mapped[list["Scene"]] = relationship(
        primaryjoin="and_(Script.id == Scene.script_id, Scene.script_position.is_not(None), Scene.deleted_at.is_(None))",
        order_by="Scene.script_position", viewonly=True,
    )

    @property
    def content_blocks(self):
        if self.legacy_blocks:
            return self.legacy_blocks
        return [block for scene in self.screenplay for block in scene.script_blocks]

    @content_blocks.setter
    def content_blocks(self, blocks):
        # Older imports can still construct a Script from a complete document.
        self.legacy_blocks = blocks
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
    legacy_body: Mapped[str] = mapped_column("body", Text, default="")
    script_anchor: Mapped[str | None] = mapped_column(Text)
    script_position: Mapped[int | None] = mapped_column(Integer)
    script_blocks: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))
    elements_revision: Mapped[str | None] = mapped_column(String(64))

    @property
    def removed_from_script(self):
        return self.script_anchor is not None and self.script_position is None

    @property
    def body(self):
        if self.script_blocks is None:
            return self.legacy_body
        return render_scene_body(self.script_blocks)

    @body.setter
    def body(self, value):
        self.legacy_body = value
    source_key: Mapped[str | None] = mapped_column(String(64), index=True)
    visual_brief: Mapped[str] = mapped_column(Text, default="", server_default="")
    adapted_from_chapter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapter.id")
    )
    status: Mapped[VersionStatus] = mapped_column(String(16), default=VersionStatus.draft)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (
        Index("ix_scene_script_ordinal", "script_id", "ordinal"),
        Index("uq_scene_script_anchor", "script_id", "script_anchor", unique=True),
    )


def render_scene_body(blocks):
    lines = []
    for block in blocks:
        text = block["text"]
        kind = block["block_type"]
        if kind == "scene_heading":
            continue
        if kind == "character":
            text = f"{text}:"
        elif kind == "parenthetical":
            text = f"({text.strip('()')})"
        lines.append(text)
    return "\n".join(lines)


class SceneElement(Base, TimestampMixin):
    """One on-screen use of a person, location or prop; the project asset is reusable."""
    __tablename__ = "scene_element"

    id: Mapped[uuid.UUID] = uuid_pk()
    scene_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("scene.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    name_key: Mapped[str] = mapped_column(Text)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("asset.id"), index=True)
    manual_binding: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    evidence: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    direction: Mapped[str] = mapped_column(Text, default="")
    reference_hash: Mapped[str | None] = mapped_column(String(128), ForeignKey("blob.hash"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("scene_id", "kind", "name_key", name="uq_scene_element"),)


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
