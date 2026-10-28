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


class AttachmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attachment_id: uuid.UUID
    role: Literal["reference", "first_frame", "last_frame"] = "reference"


class ProductionControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: uuid.UUID
    action: Literal["steer", "reply", "approve_plan", "guidance", "confirm_guidance"]
    expected_revision: int = Field(ge=1)
    task_ids: list[uuid.UUID] = Field(default_factory=list, max_length=256)
    budget_points: int | None = Field(default=None, ge=0, le=1000000)
    guidance_id: uuid.UUID | None = None


class Send(BaseModel):
    content: str = Field(default="", max_length=8000)
    request_key: uuid.UUID
    model_provider: str | None = Field(default=None, max_length=120)
    mode: Literal["standard", "test"] = "standard"
    scope: list[Target] = Field(default_factory=list, max_length=12)
    skills: list[SkillUse] = Field(default_factory=list, max_length=3)
    media: MediaRequest | None = None
    production: ProductionControl | None = None
    attachments: list[AttachmentInput] = Field(default_factory=list, max_length=8)
    references: list[Reference] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def media_controls(self):
        if not self.content:
            if self.media or self.production or not (self.attachments or self.references):
                raise ValueError("请填写消息或添加要讨论的附件；生成时请描述创作要求")
            self.content = "请查看这些附件，概括内容并告诉我可以怎样继续使用。"
        if len(self.attachments) + len(self.references) > 8:
            raise ValueError("每条消息最多添加 8 项参考内容")
        if len({a.attachment_id for a in self.attachments}) != len(self.attachments):
            raise ValueError("同一附件不能重复添加")
        if self.media and self.references:
            raise ValueError("生成参考请使用本轮生成的参考内容")
        if self.production and self.media:
            raise ValueError("任务指导和单独生成请分别发送")
        if self.media and (self.mode != "standard" or self.scope):
            raise ValueError("直接生成使用标准模式，不修改已选创作对象")
        if self.media and len(self.content) > 6000:
            raise ValueError("图片和视频提示词最多6000字")
        return self

    @field_validator("content")
    @classmethod
    def clean_content(cls, value):
        return value.strip()


class Control(BaseModel):
    action: Literal["pause", "resume", "cancel"]
    additional_turns: int = Field(default=0, ge=0, le=12)


class Retry(BaseModel):
    request_key: uuid.UUID
