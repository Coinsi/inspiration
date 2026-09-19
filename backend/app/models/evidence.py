"""Project character identities and separately reviewed visual/dialogue evidence."""

import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class StoryIdentity(Base, TimestampMixin):
    __tablename__ = "story_identity"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    name: Mapped[str] = mapped_column(String(120))
    aliases: Mapped[list[str]] = mapped_column(JSONB, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("asset.id"))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)


class IdentityEvidence(Base, TimestampMixin):
    __tablename__ = "identity_evidence"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID, ForeignKey("story_identity.id"), index=True
    )
    version_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("media_version.id"), index=True)
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    observation: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[dict] = mapped_column(JSONB, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)


class EvidenceRevision(Base, TimestampMixin):
    __tablename__ = "evidence_revision"
    id: Mapped[uuid.UUID] = uuid_pk()
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("identity_evidence.id", ondelete="CASCADE")
    )
    revision: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID)
    __table_args__ = (Index("uq_evidence_revision", "evidence_id", "revision", unique=True),)
