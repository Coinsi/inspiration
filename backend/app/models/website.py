"""Independent platform website drafts, immutable releases and explicit public media."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class Website(Base, TimestampMixin):
    __tablename__ = "website"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    draft: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published: Mapped[dict] = mapped_column(JSONB, nullable=False)


class WebsiteRelease(Base, TimestampMixin):
    __tablename__ = "website_release"
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    note: Mapped[str] = mapped_column(String(300), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), nullable=False
    )


class WebsiteMedia(Base, TimestampMixin):
    __tablename__ = "website_media"
    id: Mapped[uuid.UUID] = uuid_pk()
    blob_hash: Mapped[str] = mapped_column(String(64), ForeignKey("blob.hash"), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), nullable=False
    )


class BlogPost(Base, TimestampMixin):
    __tablename__ = "blog_post"
    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    draft: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
