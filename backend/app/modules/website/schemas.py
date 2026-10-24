import uuid
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class Banner(Strict):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=200)
    media_id: uuid.UUID | None = None
    href: str = Field(default="", max_length=1000)
    enabled: bool = True

    @field_validator("href")
    @classmethod
    def safe_link(cls, value):
        if not value:
            return value
        if any(ord(c) < 32 for c in value) or "\\" in value:
            raise ValueError("链接不能包含控制字符或反斜线")
        if value.startswith("/") and not value.startswith("//"):
            return value
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("请填写站内路径或完整 HTTPS 链接")
        return value


def initial_banners():
    return [
        Banner(
            id="canvas",
            title="让想法，在画布上相遇",
            description="整理故事、角色与镜头，开启你的创作。",
            href="/projects",
        ),
        Banner(
            id="journal",
            title="每一个故事，都有新的开始",
            description="创作指南、产品更新与灵感笔记。",
            href="/blog",
        ),
        Banner(
            id="studio",
            title="从第一幕，到最后一个镜头",
            description="在同一个工作台里，让故事逐渐成形。",
            href="/projects",
        ),
        Banner(
            id="ideas",
            title="把灵感，留给下一部作品",
            description="从素材到故事，发现新的创作可能。",
            href="/projects",
        ),
    ]


class Content(Strict):
    banners: list[Banner] = Field(default_factory=initial_banners, max_length=12)
    banner_autoplay: bool = True
    banner_interval: int = Field(default=6, ge=3, le=30)
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
        if len({b.id for b in self.banners}) != len(self.banners):
            raise ValueError("轮播图标识不能重复")
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
