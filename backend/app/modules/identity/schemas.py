"""identity DTO(请求/响应模型)。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import Role


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    username: str
    email: EmailStr
    display_name: str
    is_active: bool


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=6)
    display_name: str


class ProjectIn(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    settings: dict = Field(default_factory=dict)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    cover_blob_hash: str | None = None
    deleted_at: datetime | None = None
    owner_id: uuid.UUID
    settings: dict
    created_at: datetime


class MemberIn(BaseModel):
    user_id: uuid.UUID
    role: Role


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    role: Role


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MemberOut]


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    action: str
    user_id: uuid.UUID | None
    target_type: str | None
    target_id: uuid.UUID | None
    detail: dict
    created_at: datetime
