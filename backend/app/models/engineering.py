"""Work revisions, pinned resources and reviewed timeline candidates."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class EngineeringCommit(Base, TimestampMixin):
    __tablename__ = "engineering_commit"
    id: Mapped[uuid.UUID] = uuid_pk()
    work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("creative_work.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    parent_revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)
    __table_args__ = (UniqueConstraint("work_id", "revision"),)


class EngineeringChange(Base, TimestampMixin):
    __tablename__ = "engineering_change"
    id: Mapped[uuid.UUID] = uuid_pk()
    work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("creative_work.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)
    request_key: Mapped[uuid.UUID] = mapped_column(UUID)
    fingerprint: Mapped[str] = mapped_column(String(64))
    base_revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    committed_revision: Mapped[int | None] = mapped_column(Integer)
    timeline_update: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    preview_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generation_job.id"))
    __table_args__ = (UniqueConstraint("work_id", "created_by", "request_key"),)


class EngineeringResource(Base, TimestampMixin):
    """Pinned sources retained for candidates AND history, not a second media library."""

    __tablename__ = "engineering_resource"
    id: Mapped[uuid.UUID] = uuid_pk()
    work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("creative_work.id"), index=True)
    source_kind: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[uuid.UUID] = mapped_column(UUID)
    media_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_version.id"), index=True
    )
    generation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generation.id"))
    blob_hash: Mapped[str] = mapped_column(ForeignKey("blob.hash"))
    media_type: Mapped[str] = mapped_column(String(16))
    __table_args__ = (UniqueConstraint("work_id", "source_kind", "source_id"),)


class EngineeringDocument(Base, TimestampMixin):
    """Stable editable identity. Content lives only in immutable revisions."""

    __tablename__ = "engineering_document"
    id: Mapped[uuid.UUID] = uuid_pk()
    work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("creative_work.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(255))
    source_timeline_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("timeline.id"))
    __table_args__ = (UniqueConstraint("work_id", "source_timeline_id"),)


class EngineeringDocumentRevision(Base, TimestampMixin):
    __tablename__ = "engineering_document_revision"
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("engineering_document.id"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64))
    inputs: Mapped[dict] = mapped_column(JSONB)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)


class EngineeringDelivery(Base, TimestampMixin):
    """Transactional handoff for local engineering render jobs, until worker claim."""

    __tablename__ = "engineering_delivery"
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generation_job.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    last_error: Mapped[str | None] = mapped_column(String(255))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class EngineeringArchiveJob(Base, TimestampMixin):
    __tablename__ = "engineering_archive_job"
    id: Mapped[uuid.UUID] = uuid_pk()
    work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("creative_work.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)
    request_key: Mapped[uuid.UUID] = mapped_column(UUID)
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    progress: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(String(255))
    operation: Mapped[str] = mapped_column(String(16), default="export", server_default="export")
    expected_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    uploaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    filename: Mapped[str | None] = mapped_column(String(255))
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    __table_args__ = (UniqueConstraint("work_id", "created_by", "request_key"),)
