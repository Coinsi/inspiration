import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.modules.skill.schemas import SkillUse


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: Literal["script", "scene", "shot", "asset", "setting"]
    target_id: uuid.UUID


class Create(BaseModel):
    goal: str = Field(min_length=1, max_length=8000)
    engine: Literal["cloud_llm", "mock"] = "cloud_llm"
    scope: list[Target] = Field(default_factory=list, max_length=12)
    skills: list[uuid.UUID] = Field(default_factory=list, max_length=3)
    skill_versions: list[SkillUse] = Field(default_factory=list, max_length=3)
    max_turns: int = Field(default=12, ge=1, le=24)
    request_key: uuid.UUID


class Decision(BaseModel):
    action: Literal["approve", "reject"]
    note: str = Field(default="", max_length=1000)


class Control(BaseModel):
    action: Literal["pause", "resume", "cancel"]
    additional_turns: int = Field(default=0, ge=0, le=12)


class ModelAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(min_length=1, max_length=80)
    arguments: dict = Field(default_factory=dict)
    message: str = Field(default="", max_length=4000)


class ToolInvocation(BaseModel):
    tool: str = Field(min_length=1, max_length=80)
    arguments: dict = Field(default_factory=dict)
    request_key: uuid.UUID | None = None
