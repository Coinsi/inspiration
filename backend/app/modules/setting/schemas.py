"""setting DTO。"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SettingIn(BaseModel):
    novel_id: uuid.UUID | None = None
    category: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    content: str = ""


class SettingUpdate(BaseModel):
    category: str | None = None
    name: str | None = None
    content: str | None = None


class SettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    novel_id: uuid.UUID | None
    category: str
    name: str
    content: str
    source_chapters: list
    created_at: datetime
    updated_at: datetime


class ExtractIn(BaseModel):
    """提取范围(章节序号,1 起,闭区间);均为空 = 全书。concurrency 为并发章节数。"""
    from_chapter: int | None = Field(None, ge=1)
    to_chapter: int | None = Field(None, ge=1)
    concurrency: int = Field(4, ge=1, le=8)


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    novel_id: uuid.UUID
    status: str
    total_chapters: int
    done_chapters: int
    error: str | None
    created_at: datetime
