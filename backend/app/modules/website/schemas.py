import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Feature(Strict):
    key: Literal["story", "canvas", "library", "edit"]
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=350)
    media_id: uuid.UUID | None = None


class FAQ(Strict):
    question: str = Field(min_length=1, max_length=150)
    answer: str = Field(min_length=1, max_length=700)


class Section(Strict):
    key: Literal["workflow", "features", "case", "faq"]
    enabled: bool = True


class Content(Strict):
    brand: str = Field(min_length=1, max_length=40)
    announcement: str = Field(max_length=120)
    eyebrow: str = Field(max_length=100)
    title: str = Field(min_length=1, max_length=140)
    subtitle: str = Field(min_length=1, max_length=350)
    cta: str = Field(min_length=1, max_length=30)
    hero_media_id: uuid.UUID | None = None
    workflow_title: str = Field(min_length=1, max_length=100)
    workflow_description: str = Field(min_length=1, max_length=350)
    features_title: str = Field(min_length=1, max_length=100)
    features: list[Feature] = Field(min_length=4, max_length=4)
    case_title: str = Field(min_length=1, max_length=100)
    case_description: str = Field(min_length=1, max_length=700)
    case_media_id: uuid.UUID | None = None
    faq: list[FAQ] = Field(min_length=1, max_length=12)
    closing_title: str = Field(min_length=1, max_length=120)
    footer: str = Field(max_length=200)
    sections: list[Section] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def unique_sections(self):
        if len({s.key for s in self.sections}) != 4 or len({f.key for f in self.features}) != 4:
            raise ValueError("板块与功能不能重复")
        return self


class Save(Strict):
    version: int = Field(ge=1)
    content: Content


class Publish(Strict):
    version: int = Field(ge=1)
    note: str = Field(default="内容更新", min_length=1, max_length=300)


class Restore(Strict):
    version: int = Field(ge=1)
