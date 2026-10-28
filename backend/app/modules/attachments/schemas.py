import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class UploadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: uuid.UUID
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("filename")
    @classmethod
    def clean_filename(cls, value):
        # File names are labels, never paths. Normalize pasted OS file names.
        value = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("文件名无效")
        return value


class UpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=20)
    expected_revision: int | None = Field(default=None, ge=1)
    favorite: bool | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if value is not None and not value.strip():
            raise ValueError("名称不能为空")
        return value.strip() if value is not None else None

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values):
        if values is None:
            return None
        result = []
        for value in values:
            value = value.strip()
            if not value or len(value) > 40:
                raise ValueError("标签须为 1 至 40 个字符")
            if value not in result:
                result.append(value)
        return result

    @model_validator(mode="after")
    def require_revision(self):
        if any(value is not None for value in (self.title, self.description, self.tags)):
            if self.expected_revision is None:
                raise ValueError("修改附件信息需要当前版本号")
        return self
