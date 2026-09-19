"""FastAPI 依赖:当前用户、项目上下文、权限校验。"""
import uuid
from dataclasses import dataclass

from fastapi import Depends, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import Forbidden, Unauthorized
from app.core.permissions import require
from app.core.security import decode_access_token
from app.models.enums import Role
from app.models.identity import Membership, Project, User

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db, scope="function"),
) -> User:
    if cred is None:
        raise Unauthorized("缺少认证令牌")
    try:
        payload = decode_access_token(cred.credentials)
        user_id = uuid.UUID(payload["sub"])
    except Exception as exc:  # noqa: BLE001
        raise Unauthorized("令牌无效或已过期") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("用户不存在或已禁用")
    return user


@dataclass
class ProjectContext:
    project: Project
    membership: Membership
    user: User

    @property
    def role(self) -> Role:
        # DB 以字符串存枚举值,读回为 str,统一强制转回 Role
        r = self.membership.role
        return r if isinstance(r, Role) else Role(r)


def get_project_context(
    project_id: uuid.UUID = Path(..., alias="project_id"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
) -> ProjectContext:
    project = db.get(Project, project_id)
    if project is None or project.deleted_at is not None:
        raise Forbidden("项目不存在或无权访问")
    membership = db.scalar(
        select(Membership).where(Membership.project_id == project_id, Membership.user_id == user.id)
    )
    if membership is None:
        raise Forbidden("你不是该项目成员")
    return ProjectContext(project=project, membership=membership, user=user)


def require_action(action: str):
    """依赖工厂:要求当前用户在项目内具备某动作权限。"""

    def _checker(ctx: ProjectContext = Depends(get_project_context)) -> ProjectContext:
        require(ctx.role, action)
        return ctx

    return _checker
