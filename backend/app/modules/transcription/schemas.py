import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Create(BaseModel):
    version_id: uuid.UUID
    language: Literal["zh", "en", "ja", "ko", "fr", "de", "es"] | None = None
    new_run: bool = False


class Cue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_ms - self.start_ms < 100 or not self.text.strip():
            raise ValueError("字幕不能为空且至少持续0.1秒")
        return self


class Save(BaseModel):
    revision: int = Field(ge=0)
    cues: list[Cue] = Field(max_length=10000)

    @model_validator(mode="after")
    def unique(self):
        if len({c.id for c in self.cues}) != len(self.cues):
            raise ValueError("字幕编号重复")
        if sum(len(c.text) for c in self.cues) > 900000:
            raise ValueError("字幕过大")
        return self


class Revision(BaseModel):
    revision: int = Field(ge=0)


class Control(BaseModel):
    action: Literal["cancel", "retry"]
