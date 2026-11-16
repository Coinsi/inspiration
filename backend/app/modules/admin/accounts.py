"""Account changes serialize the last-super-admin invariant, including CLI changes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text

from app.core import audit
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.platform_permissions import role
from app.models.identity import User


class AccountChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    action: Literal["disable", "enable", "revoke_sessions", "set_role"]
    role: Literal["user", "admin", "super"] | None = None
    reason: str = Field(min_length=1, max_length=300)


def change(db, actor_id, user_id, data, *, bootstrap=False):
    # One lock covers all accounts; row locks alone permit concurrent last-admin removal.
    db.execute(text("SELECT pg_advisory_xact_lock(18423, 57)"))
    if not bootstrap:
        actor = db.scalar(
            select(User).where(User.id == actor_id).execution_options(populate_existing=True)
        )
        if not actor or not actor.is_active or role(actor) != "super":
            raise Forbidden("仅有效的超级管理员可修改账号")
    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not user:
        raise NotFound("用户不存在")
    if user.admin_revision != data.expected_revision:
        raise Conflict("账号已被其他管理员修改，请刷新后重试")
    if not data.reason.strip():
        raise Conflict("请填写操作原因")
    before = {"role": role(user), "is_active": user.is_active}
    next_role = data.role if data.action == "set_role" else role(user)
    if next_role is None:
        raise Conflict("请选择账号角色")
    next_active = {"disable": False, "enable": True}.get(data.action, user.is_active)
    if user.is_active and role(user) == "super" and (not next_active or next_role != "super"):
        remaining = db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.id != user.id,
                User.is_active.is_(True),
                User.is_platform_admin.is_(True),
                (User.platform_role == "super") | User.platform_role.is_(None),
            )
        )
        if not remaining:
            raise Conflict("必须保留至少一位有效的超级管理员，请先授权另一位管理员")
    user.is_active = next_active
    user.is_platform_admin = next_role != "user"
    user.platform_role = None if next_role == "user" else next_role
    # Any administrative security change revokes existing sessions, including role changes.
    user.auth_version += 1
    user.admin_revision += 1
    audit.record(
        db,
        user_id=actor_id,
        action=f"admin.user.{data.action}",
        target_type="user",
        target_id=user.id,
        detail={
            "before": before,
            "after": {"role": next_role, "is_active": next_active},
            "reason": data.reason.strip(),
        },
    )
    db.flush()
    return user


def out(user):
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "is_active": user.is_active,
        "role": role(user),
        "revision": user.admin_revision,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }
