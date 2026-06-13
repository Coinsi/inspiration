"""review:评审、批注、返修轮次。"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk
from app.models.enums import AnnotationKind, ReviewStatus


class Review(Base, TimestampMixin):
    __tablename__ = "review"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    target_type: Mapped[str] = mapped_column(String(16), default="shot")  # shot/generation
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    generation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[ReviewStatus] = mapped_column(String(16), default=ReviewStatus.pending)
    round_no: Mapped[int] = mapped_column(Integer, default=1)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decision: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_review_target", "target_type", "target_id"),)


class Annotation(Base, TimestampMixin):
    __tablename__ = "annotation"

    id: Mapped[uuid.UUID] = uuid_pk()
    review_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("review.id", ondelete="CASCADE"))
    kind: Mapped[AnnotationKind] = mapped_column(String(16), default=AnnotationKind.comment)
    geometry: Mapped[dict | None] = mapped_column(JSONB)  # 画面框选坐标
    timecode_ms: Mapped[int | None] = mapped_column(Integer)  # 视频批注
    comment: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Revision(Base, TimestampMixin):
    __tablename__ = "revision"

    id: Mapped[uuid.UUID] = uuid_pk()
    review_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("review.id", ondelete="CASCADE"))
    shot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shot.id"))
    round_no: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str] = mapped_column(Text, default="")
