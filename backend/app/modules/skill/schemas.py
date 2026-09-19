import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SkillFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=1_400_000)
    encoding: Literal["utf-8", "base64"] = "utf-8"

    @field_validator("path")
    @classmethod
    def safe_path(cls, value):
        from app.modules.skill.packages import safe_path

        return safe_path(value)


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["manual", "file", "github", "copy"] = "manual"
    url: str = Field(default="", max_length=1000)
    ref: str = Field(default="", max_length=200)
    directory: str = Field(default="", max_length=240)
    commit: str = Field(default="", max_length=80)
    origin_id: str = Field(default="", max_length=80)


class SkillIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    instructions: str = Field(min_length=1, max_length=20000)
    required_tools: list[str] = Field(default_factory=list, max_length=16)
    source: str = Field(default="手动编写", max_length=1000)

    category: Literal["general", "story", "character", "shot", "edit"] = "general"
    files: list[SkillFile] = Field(default_factory=list, max_length=40)
    source_metadata: SourceMetadata = Field(default_factory=SourceMetadata)

    @model_validator(mode="after")
    def package_bounds(self):
        import base64

        total, paths = 0, set()
        for f in self.files:
            if f.path.casefold() in paths or f.path.casefold() == "skill.md":
                raise ValueError("配套文件名不能重复，入口正文请使用方法与步骤字段")
            paths.add(f.path.casefold())
            try:
                payload = (
                    base64.b64decode(f.content, validate=True)
                    if f.encoding == "base64"
                    else f.content.encode("utf-8")
                )
            except ValueError:
                raise ValueError("文件编码无效") from None
            if len(payload) > 1_000_000:
                raise ValueError("单个配套文件不能超过1MB")
            total += len(payload)
        if total > 4_000_000:
            raise ValueError("配套文件总量不能超过4MB")
        return self

    @field_validator("required_tools")
    @classmethod
    def known_tools(cls, values):
        from app.modules.agent.tools import CATALOG

        if any(v not in CATALOG for v in values):
            raise ValueError("技能引用了未登记工具")
        return list(dict.fromkeys(values))


class Save(SkillIn):
    revision: int = Field(ge=1)


class Archive(BaseModel):
    revision: int = Field(ge=1)
    archived: bool


class Restore(BaseModel):
    revision: int = Field(ge=1)


class SourceItem(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: Literal["character", "prop", "location", "costume", "vehicle", "style"] = "prop"
    summary: str = Field(default="", max_length=10000)
    source_id: str = Field(default="", max_length=255)
    source_url: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, v):
        if any(len(t) > 80 for t in v):
            raise ValueError("标签过长")
        return v

    @field_validator("source_url")
    @classmethod
    def valid_url(cls, v):
        if v and not v.startswith(("https://", "http://")):
            raise ValueError("来源链接必须为HTTP或HTTPS")
        return v


class ImportIn(BaseModel):
    request_key: uuid.UUID
    source: str = Field(min_length=1, max_length=1000)
    items: list[SourceItem] = Field(min_length=1, max_length=100)


class GitHubImport(BaseModel):
    url: str = Field(min_length=1, max_length=1000)
    ref: str = Field(default="", max_length=200)
    directory: str = Field(default="", max_length=240)


class CopyIn(BaseModel):
    source_project_id: uuid.UUID
    source_skill_id: uuid.UUID
    revision: int = Field(ge=1)


class SkillUse(BaseModel):
    id: uuid.UUID
    revision: int = Field(ge=1)


class Install(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: uuid.UUID
    document: SkillIn
    # Display cache for persisted canvas chips; execution reads the version's own name.
    name: str | None = Field(default=None, max_length=120)
