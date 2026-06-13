"""shot:镜头(核心生产单元)+ 镜头→资产引用热路径。"""
import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CodedMixin, ProjectScopedMixin, SoftDeleteMixin, TimestampMixin, uuid_pk
from app.models.enums import ProductionStatus, RefMode, VersionStatus


class Shot(Base, CodedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "shot"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    scene_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("scene.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    production_status: Mapped[ProductionStatus] = mapped_column(
        String(24), default=ProductionStatus.to_design
    )
    prompt_override: Mapped[str | None] = mapped_column(Text)
    # 分镜规格(景别/机位/运镜/时长/对白/转场/音乐音效/画面比例/节奏/备注…),JSON 可扩展
    storyboard: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    selected_generation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id"))
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id"))
    status: Mapped[VersionStatus] = mapped_column(String(16), default=VersionStatus.draft)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (
        Index("ix_shot_scene_ordinal", "scene_id", "ordinal"),
        Index("ix_shot_board", "project_id", "production_status"),
    )


class ShotAssetRef(Base, TimestampMixin):
    """镜头→资产 引用(承载浮动/钉死语义)。"""

    __tablename__ = "shot_asset_ref"

    id: Mapped[uuid.UUID] = uuid_pk()
    shot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shot.id", ondelete="CASCADE"))
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("asset.id"), index=True)
    ref_mode: Mapped[RefMode] = mapped_column(String(16), default=RefMode.floating)
    pinned_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(32), default="character")
    ordinal: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("shot_id", "asset_id", "role", name="uq_shot_asset_ref"),)
