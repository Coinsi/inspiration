import uuid
from typing import Literal

from pydantic import BaseModel, Field


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
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    purpose: Literal["visual_reference", "editing_source"] = "visual_reference"
    note: str = Field(default="", max_length=1000)


class MaterializeIn(BaseModel):
    request_key: uuid.UUID
    output_type: Literal["image", "video", "audio"]
    time_ms: int | None = Field(default=None, ge=0)


class MediaSearchIn(BaseModel):
    query: str = Field(default="", max_length=2000)
    image: str | None = Field(default=None, max_length=3_000_000)
    mode: Literal["semantic", "annotated"] = "semantic"
    kind: Literal["visual", "speech"] | None = None
    offset: int = Field(default=0, ge=0, le=1_000_000)
    limit: int = Field(default=24, ge=1, le=100)
    collapse_versions: bool = False


class AnnotationIn(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    kind: Literal["visual", "speech"] = "visual"
    text: str = Field(min_length=1, max_length=4000)


class SubtitleImportIn(BaseModel):
    content: str = Field(min_length=1, max_length=1_000_000)
