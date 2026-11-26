"""narrative DTO。"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


from app.adapters.contracts import StoryboardShotSuggestion


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
    cover_blob_hash: str | None = None
    source_format: str | None
    created_at: datetime


class ChapterSummaryOut(BaseModel):
    id: uuid.UUID
    ordinal: int
    title: str | None


class NovelDirectoryOut(NovelOut):
    chapters: list[ChapterSummaryOut] = Field(default_factory=list)


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
    script_id: uuid.UUID | None = None
    script_title: str | None = None
    removed_from_script: bool = False
    chapter_id: uuid.UUID | None = None
    chapter_ordinal: int | None = None
    chapter_title: str | None = None
    novel_id: uuid.UUID | None = None
    novel_title: str | None = None


class ScriptIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, value):
        return value.strip() if isinstance(value, str) else value


class ScriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    title: str
    cover_blob_hash: str | None = None
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
    cover_blob_hash: str | None = None
    created_at: datetime
    source_chapter_id: uuid.UUID | None = None
    content_blocks: list[ScriptBlockDTO] = Field(default_factory=list)
    content_revision: str


class GenerateScriptIn(BaseModel):
    """从章节改编生成剧本正文(自动新建一条 Script)。"""

    chapter_id: uuid.UUID
    title: str | None = None
    strategy: str | None = None


class UpdateScriptBlocksIn(BaseModel):
    blocks: list[ScriptBlockDTO]
    expected_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class SceneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    ordinal: int
    title: str | None
    summary: str | None
    adapted_from_chapter_id: uuid.UUID | None
    removed_from_script: bool = False


class SceneElementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    name: str
    asset_id: uuid.UUID | None
    evidence: str
    prompt: str
    direction: str
    reference_hash: str | None


class SceneElementEdit(BaseModel):
    asset_id: uuid.UUID | None = None
    reference_hash: str | None = Field(default=None, max_length=128)
    direction: str | None = Field(default=None, max_length=6000)


class AppliedSceneOut(SceneOut):
    scene_created: bool
    shots_added: int
    shots_kept: int


class ShotBrief(StoryboardShotSuggestion):
    pass


class SceneSuggestionOut(BaseModel):
    scene_id: uuid.UUID | None = None
    source_revision: str | None = None
    breakdown_status: Literal["pending", "running", "succeeded", "failed"] | None = None
    error: str | None = None
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


class ConfirmScriptSource(BaseModel):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class ShotPromptRevision(ConfirmScriptSource):
    shot_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    still_prompt: str = Field(min_length=1, max_length=12000)
    motion_prompt: str = Field(min_length=1, max_length=12000)


class EntityDraftOut(BaseModel):
    type: str
    name: str
    summary: str = ""
    evidence: str = ""
    asset_id: uuid.UUID | None = None
    separate: bool = False
    scene_id: uuid.UUID | None = None
    element_id: uuid.UUID | None = None
    scene_title: str | None = None


class ExtractEntitiesIn(BaseModel):
    script_id: uuid.UUID | None = None
    chapter_id: uuid.UUID | None = None
    text: str | None = None


class ApplyEntitiesIn(BaseModel):
    entities: list[EntityDraftOut]
    script_id: uuid.UUID | None = None
    chapter_id: uuid.UUID | None = None
