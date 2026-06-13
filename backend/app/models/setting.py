"""setting:故事圣经(设定库)。AI 通读小说沉淀的世界观/体系/人物/地点等设定。

category 为自由字符串(预置 8 类,可扩展);(project, novel, category, name) 唯一,
支撑逐章增量提取时的合并 upsert。SettingExtraction 是一键全书提取的后台任务。
"""
import uuid

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ProjectScopedMixin, TimestampMixin, uuid_pk


class Setting(Base, TimestampMixin):
    __tablename__ = "setting"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    novel_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    # 来源章节序号列表(溯源:这条设定出自哪些章)
    source_chapters: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint("project_id", "novel_id", "category", "name", name="uq_setting_key"),
        Index("ix_setting_project_category", "project_id", "category"),
    )


class SettingExtraction(Base, TimestampMixin):
    """一键全书提取任务:后台线程逐章处理,进度可轮询、可取消、可续跑。"""

    __tablename__ = "setting_extraction"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = ProjectScopedMixin.project_fk()
    novel_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/succeeded/failed/cancelled
    total_chapters: Mapped[int] = mapped_column(Integer, default=0)
    done_chapters: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
