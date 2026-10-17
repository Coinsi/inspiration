import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.generation.workbench import Reference
from app.modules.skill.schemas import SkillUse
from app.modules.tools.contracts import Target


class Create(BaseModel):
    work_id: uuid.UUID | None = None
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    title: str = Field(default="新的对话", min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class Update(BaseModel):
    work_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if value is not None and not value.strip():
            raise ValueError("名称不能为空")
        return value.strip() if value is not None else None


class MediaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=120)
    request_type: Literal["image", "video"]
    provider_params: dict[str, str | int | float | bool] = Field(default_factory=dict)
    references: list[Reference] = Field(default_factory=list, max_length=8)


class Send(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    request_key: uuid.UUID
    model_provider: str | None = Field(default=None, max_length=120)
    mode: Literal["standard", "test"] = "standard"
    scope: list[Target] = Field(default_factory=list, max_length=12)
    skills: list[SkillUse] = Field(default_factory=list, max_length=3)
    media: MediaRequest | None = None

    @model_validator(mode="after")
    def media_controls(self):
        if self.media and (self.mode != "standard" or self.scope):
            raise ValueError("直接生成使用标准模式，不修改已选创作对象")
        if self.media and len(self.content) > 6000:
            raise ValueError("图片和视频提示词最多6000字")
        return self

    @field_validator("content")
    @classmethod
    def clean_content(cls, value):
        if not value.strip():
            raise ValueError("消息不能为空")
        return value.strip()


class Control(BaseModel):
    action: Literal["pause", "resume", "cancel"]
    additional_turns: int = Field(default=0, ge=0, le=12)


class Retry(BaseModel):
    request_key: uuid.UUID
