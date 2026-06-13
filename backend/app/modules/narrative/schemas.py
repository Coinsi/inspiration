"""narrative DTO。"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChapterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ordinal: int
    title: str | None
    content: str


class NovelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    title: str
    source_format: str | None
    created_at: datetime


class NovelDetailOut(NovelOut):
    chapters: list[ChapterOut] = Field(default_factory=list)


class NovelUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class SceneListItem(BaseModel):
    """分镜工作台用:全项目场景 + 镜头数 + 章节/小说上下文。"""
    id: uuid.UUID
    code: str
    ordinal: int
    title: str | None
    summary: str | None
    shot_count: int = 0
    chapter_id: uuid.UUID | None = None
    chapter_ordinal: int | None = None
    chapter_title: str | None = None
    novel_id: uuid.UUID | None = None
    novel_title: str | None = None


class ScriptIn(BaseModel):
    title: str


class ScriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    title: str
    created_at: datetime
    block_count: int = 0  # 正文块数(列表页角标)
    scene_count: int = 0  # 场景头数


# ── 剧本正文(typed blocks)──
class ScriptBlockDTO(BaseModel):
    id: str | None = None
    block_type: str
    text: str = ""


class ScriptDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    title: str
    created_at: datetime
    source_chapter_id: uuid.UUID | None = None
    content_blocks: list[ScriptBlockDTO] = Field(default_factory=list)


class GenerateScriptIn(BaseModel):
    """从章节改编生成剧本正文(自动新建一条 Script)。"""
    chapter_id: uuid.UUID
    title: str | None = None
    strategy: str | None = None


class UpdateScriptBlocksIn(BaseModel):
    blocks: list[ScriptBlockDTO]


class SceneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    ordinal: int
    title: str | None
    summary: str | None
    adapted_from_chapter_id: uuid.UUID | None


class ShotBrief(BaseModel):
    title: str
    description: str = ""


class SceneSuggestionOut(BaseModel):
    title: str
    summary: str = ""
    body: str = ""
    source_chapter_ordinal: int | None = None
    shots: list[ShotBrief] = Field(default_factory=list)


class DecomposeOut(BaseModel):
    strategy: str
    scenes: list[SceneSuggestionOut]


class ApplyScenesIn(BaseModel):
    chapter_id: uuid.UUID | None = None
    scenes: list[SceneSuggestionOut]


class EntityDraftOut(BaseModel):
    type: str
    name: str
    summary: str = ""


class ExtractEntitiesIn(BaseModel):
    script_id: uuid.UUID | None = None
    chapter_id: uuid.UUID | None = None
    text: str | None = None


class ApplyEntitiesIn(BaseModel):
    entities: list[EntityDraftOut]
