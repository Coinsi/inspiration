"""prompt DTO。"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PromptIn(BaseModel):
    name: str
    positive: str = ""
    negative: str = ""
    tags: list[str] = Field(default_factory=list)


class PromptUpdate(BaseModel):
    name: str | None = None
    positive: str | None = None
    negative: str | None = None
    tags: list[str] | None = None


class PromptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    positive: str
    negative: str
    tags: list[str]
    created_at: datetime


class FragmentIn(BaseModel):
    category: str = "custom"  # quality/camera/lighting/style/custom
    name: str
    text: str = ""


class FragmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    category: str
    name: str
    text: str
    created_at: datetime
