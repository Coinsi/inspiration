import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class UploadIn(BaseModel):
    folder_id: uuid.UUID | None = None
    media_id: uuid.UUID | None = None
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReuseIn(BaseModel):
    folder_id: uuid.UUID | None = None
    source_project_id: uuid.UUID
    source_version_id: uuid.UUID


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: uuid.UUID | None = None


class FolderSave(FolderIn):
    revision: int = Field(ge=1)


class MediaMoveItem(BaseModel):
    media_id: uuid.UUID
    expected_folder_id: uuid.UUID | None


class MediaMoveIn(BaseModel):
    items: list[MediaMoveItem] = Field(min_length=1, max_length=100)
    folder_id: uuid.UUID | None = None


class UsageIn(BaseModel):
    version_id: uuid.UUID
    shot_id: uuid.UUID
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    purpose: Literal["visual_reference", "editing_source"] = "visual_reference"
    note: str = Field(default="", max_length=1000)


class MaterializeIn(BaseModel):
    request_key: uuid.UUID
    output_type: Literal["image", "video", "audio"]
    time_ms: int | None = Field(default=None, ge=0)


class SearchBBox(BaseModel):
    """Coordinates in the EXIF-oriented, unpadded reference image, from 0 to 1."""

    model_config = {"extra": "forbid"}
    x: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    y: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    w: float = Field(gt=0, le=1, allow_inf_nan=False, strict=True)
    h: float = Field(gt=0, le=1, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def validate_extent(self):
        if self.x + self.w > 1 or self.y + self.h > 1:
            raise ValueError("参考区域不能超出图片边界")
        return self


class MediaSearchIn(BaseModel):
    query: str = Field(default="", max_length=2000)
    image: str | None = Field(default=None, max_length=3_000_000)
    audio: str | None = Field(default=None, max_length=6_000_000)
    bbox: SearchBBox | None = None
    mode: Literal["semantic", "annotated"] = "semantic"
    kind: Literal["visual", "speech"] | None = None
    offset: int = Field(default=0, ge=0, le=1_000_000)
    limit: int = Field(default=24, ge=1, le=100)
    collapse_versions: bool = False
    engine: Literal["local", "wise"] = "local"
    target: Literal["visual", "audio", "face", "object"] = "visual"

    @model_validator(mode="after")
    def validate_reference(self):
        if self.bbox and not (
            self.image and self.engine == "wise" and self.mode == "semantic"
            and self.target in ("face", "object") and not self.query.strip() and not self.audio
        ):
            raise ValueError("参考区域仅用于人物或物体检索的参考图片")
        if self.engine == "wise" and self.mode == "semantic" and self.target == "face":
            if not self.image or self.query.strip():
                raise ValueError("人物检索需要提供人脸参考图")
        if self.audio and (self.image or self.query.strip()):
            raise ValueError("参考音频须单独使用，不能同时提交文字或图片")
        if self.audio and (
            self.engine != "wise" or self.target != "audio" or self.mode != "semantic"
        ):
            raise ValueError("参考音频仅用于 WISE 声音检索")
        return self


class AnnotationIn(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    kind: Literal["visual", "speech"] = "visual"
    text: str = Field(min_length=1, max_length=4000)


class SubtitleImportIn(BaseModel):
    content: str = Field(min_length=1, max_length=1_000_000)


class ProcessingIn(BaseModel):
    provider: Literal["wise", "mediacms"]
    capabilities: list[Literal["visual", "audio", "face", "object", "preview", "speech"]] = Field(
        min_length=1, max_length=6
    )


class MaterialProfileIn(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    revision: int = Field(ge=1)
    version_id: uuid.UUID | None = None

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values):
        result = []
        for value in values:
            value = value.strip()
            if not value or len(value) > 40:
                raise ValueError("标签须为1至40个字符")
            if value not in result:
                result.append(value)
        return result
