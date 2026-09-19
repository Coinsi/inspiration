"""Project media, immutable source versions and precise shot usages."""

import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, SoftDeleteMixin, TimestampMixin, uuid_pk


class LibraryMedia(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "library_media"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    name: Mapped[str] = mapped_column(String(255))
    folder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_folder.id"), index=True)


class MediaFolder(Base, TimestampMixin):
    __tablename__ = "media_folder"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_folder.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    revision: Mapped[int] = mapped_column(Integer, default=1)


class MediaVersion(Base, TimestampMixin):
    __tablename__ = "media_version"
    id: Mapped[uuid.UUID] = uuid_pk()
    media_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("library_media.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    fingerprint: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    uploaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    chunk_hashes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default="uploading", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    original_hash: Mapped[str | None] = mapped_column(ForeignKey("blob.hash"))
    proxy_hash: Mapped[str | None] = mapped_column(ForeignKey("blob.hash"))
    poster_hash: Mapped[str | None] = mapped_column(ForeignKey("blob.hash"))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("media_id", "ordinal", name="uq_media_version_ordinal"),
        CheckConstraint(
            "size_bytes > 0 AND uploaded_bytes >= 0 AND uploaded_bytes <= size_bytes",
            name="ck_media_upload_size",
        ),
    )


class MediaUsage(Base, TimestampMixin):
    __tablename__ = "media_usage"
    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_version.id"), index=True)
    shot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shot.id"), index=True)
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    purpose: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(1000), default="")
    __table_args__ = (
        CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_media_usage_range"),
        UniqueConstraint(
            "version_id", "shot_id", "start_ms", "end_ms", "purpose", name="uq_media_usage"
        ),
    )


class MediaReuse(Base, TimestampMixin):
    """A project-owned immutable version backed by the same physical media blobs."""

    __tablename__ = "media_reuse"
    id: Mapped[uuid.UUID] = uuid_pk()
    source_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_version.id"), index=True)
    target_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_version.id"), unique=True
    )
    target_project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"))
    source_name: Mapped[str] = mapped_column(String(255))
    source_ordinal: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("source_version_id", "target_project_id", name="uq_media_reuse_project"),
    )
