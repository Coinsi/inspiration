"""timeline:时间线、片段组装、成片(cut)。audio_track 为 EXT-11 占位。"""
import uuid

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CodedMixin, ProjectScopedMixin, SoftDeleteMixin, TimestampMixin, uuid_pk
from app.models.enums import CutKind, VersionStatus


class Timeline(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "timeline"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="main")  # main/sequence
    status: Mapped[VersionStatus] = mapped_column(String(16), default=VersionStatus.draft)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class TimelineItem(Base, TimestampMixin):
    __tablename__ = "timeline_item"

    id: Mapped[uuid.UUID] = uuid_pk()
    timeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timeline.id", ondelete="CASCADE")
    )
    shot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shot.id"))
    generation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    in_point_ms: Mapped[int] = mapped_column(Integer, default=0)
    out_point_ms: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    transition: Mapped[dict | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (Index("ix_timeline_item_order", "timeline_id", "ordinal"),)


class Cut(Base, CodedMixin, TimestampMixin):
    __tablename__ = "cut"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[CutKind] = mapped_column(String(16), default=CutKind.rough)
    timeline_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("timeline.id"))
    baseline_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[VersionStatus] = mapped_column(String(16), default=VersionStatus.draft)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class AudioTrack(Base, TimestampMixin):
    """EXT-11 预留:音轨层(对白/配乐/音效)。本期仅占位。"""

    __tablename__ = "audio_track"

    id: Mapped[uuid.UUID] = uuid_pk()
    timeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timeline.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16), default="dialogue")
    blob_hash: Mapped[str | None] = mapped_column(String(128), ForeignKey("blob.hash"))
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
