"""identity:用户、项目、成员、审计、编码序号。"""

import uuid

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
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
from app.models.enums import Role


class User(Base, TimestampMixin):
    __tablename__ = "user"

    id: Mapped[uuid.UUID] = uuid_pk()
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    avatar_data: Mapped[str | None] = mapped_column(Text)
    bio: Mapped[str] = mapped_column(String(500), default="", server_default="", nullable=False)
    auth_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )


class UserIdentity(Base, TimestampMixin):
    """External identities refer to a stable user; email is never a linking key."""

    __tablename__ = "user_identity"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    __table_args__ = (
        UniqueConstraint("provider", "issuer", "subject", name="uq_user_identity_subject"),
    )


class Project(Base, CodedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "project"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    cover_blob_hash: Mapped[str | None] = mapped_column(String(64), ForeignKey("blob.hash"))
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id"))
    # settings: 编码规则覆盖 / 默认策略等
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (UniqueConstraint("code", name="uq_project_code"),)


class Membership(Base, TimestampMixin):
    __tablename__ = "membership"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE")
    )
    role: Mapped[Role] = mapped_column(String(16), nullable=False)

    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_membership"),)


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)


class CodeSequence(Base):
    """项目内各实体类型的自增序号(生成业务编码)。"""

    __tablename__ = "code_sequence"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
    )
    entity_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_seq: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
