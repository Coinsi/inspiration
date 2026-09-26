"""identity DTO(请求/响应模型)。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

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
    is_platform_admin: bool = False
    avatar_data: str | None = None
    bio: str = ""


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    display_name: str = Field(min_length=1, max_length=128)

    @field_validator("username", "display_name", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("username")
    @classmethod
    def valid_username(cls, value):
        import re

        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError("用户名仅支持英文字母、数字、下划线、短横线和点")
        return value

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码不能超过72字节")
        return value


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
    user_id: uuid.UUID | None = None
    username: str | None = Field(default=None, min_length=3, max_length=64)
    role: Role

    @model_validator(mode="after")
    def one_target(self):
        if (self.user_id is None) == (self.username is None):
            raise ValueError("请填写用户名")
        return self


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    role: Role
    username: str | None = None
    display_name: str | None = None
    avatar_data: str | None = None
    is_owner: bool = False


class MemberRoleIn(BaseModel):
    role: Role


class ProfileIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)
    bio: str = Field(default="", max_length=500)

    @field_validator("display_name", "bio", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def password_bytes(cls, value):
        return RegisterIn.password_bytes(value)


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
