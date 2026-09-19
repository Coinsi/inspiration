"""assist DTO。"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from app.modules.skill.schemas import SkillUse


class CreateChatIn(BaseModel):
    target_type: str
    target_id: uuid.UUID
    title: str | None = None


class ProposalOut(BaseModel):
    summary: str = ""
    ops: list[dict] = Field(default_factory=list)
    needs_clarification: bool = False
    preview: dict | None = None  # dry-run 后的对象新状态(供前端预览/diff)
    applied_at: str | None = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: str = ""
    created_at: str | None = None
    proposal: ProposalOut | None = None
    skills: list[dict] = Field(default_factory=list)


class ChatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    title: str | None = None
    created_at: datetime
    messages: list[MessageOut] = Field(default_factory=list)


class ChatDetailOut(ChatOut):
    state: dict | None = None  # 目标对象当前状态


class PostMessageIn(BaseModel):
    content: str
    skills: list[SkillUse] = Field(default_factory=list, max_length=4)
    engine: str | None = None  # 可选:强制 mock / cloud_llm


class SendMessageOut(BaseModel):
    chat_id: uuid.UUID
    message: MessageOut  # 助手回复(含提议与预览)


class ApplyOut(BaseModel):
    applied: bool
    state: dict | None = None
    version_id: str | None = None
