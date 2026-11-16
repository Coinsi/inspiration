"""Platform permissions never imply project membership or access to private media."""

from fastapi import Depends

from app.core.deps import get_current_user
from app.core.errors import Forbidden
from app.models.identity import User


def role(user):
    if not user.is_platform_admin:
        return "user"
    return user.platform_role or "super"  # pre-migration administrators keep access


CURRENT_USER = Depends(get_current_user)


def platform_admin(user: User = CURRENT_USER):
    if role(user) not in {"super", "admin"}:
        raise Forbidden("仅平台管理员可访问管理后台")
    return user


PLATFORM_ADMIN = Depends(platform_admin)


def super_admin(user: User = PLATFORM_ADMIN):
    if role(user) != "super":
        raise Forbidden("仅超级管理员可修改账号权限和额度")
    return user
