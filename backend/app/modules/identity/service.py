"""identity 业务逻辑。"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import Conflict, Forbidden, NotFound, Unauthorized
from app.core.permissions import require
from app.core.security import create_access_token, hash_password, verify_password
from app.models.enums import Role
from app.models.identity import Membership, Project, User
from app.modules.identity import schemas


def register_user(db: Session, data: schemas.RegisterIn) -> User:
    exists = db.scalar(
        select(User).where(
            or_(User.username == data.username, func.lower(User.email) == str(data.email).lower())
        )
    )
    if exists:
        raise Conflict("用户名或邮箱已被使用")
    user = User(
        username=data.username,
        email=str(data.email).lower(),
        password_hash=hash_password(data.password),
        display_name=data.display_name,
    )
    try:
        with db.begin_nested():
            db.add(user)
            db.flush()
    except IntegrityError as exc:
        raise Conflict("用户名或邮箱已被使用") from exc
    return user


def login(db: Session, data: schemas.LoginIn) -> str:
    user = db.scalar(select(User).where(User.username == data.username))
    if (
        user is None
        or not user.password_hash
        or not verify_password(data.password, user.password_hash)
    ):
        raise Unauthorized("用户名或密码错误")
    if not user.is_active:
        raise Unauthorized("用户已禁用")
    return create_access_token(str(user.id), {"ver": user.auth_version})


def get_memberships(db: Session, user_id: uuid.UUID) -> list[Membership]:
    return list(db.scalars(select(Membership).where(Membership.user_id == user_id)))


def create_project(db: Session, owner: User, data: schemas.ProjectIn, project_id=None) -> Project:
    code = data.code or "P-" + uuid.uuid4().hex[:24].upper()
    if db.scalar(select(Project).where(Project.code == code)):
        raise Conflict("项目编码已存在")
    project = Project(
        id=project_id or uuid.uuid4(),
        code=code,
        name=data.name,
        description=data.description,
        owner_id=owner.id,
        settings=data.settings,
    )
    db.add(project)
    db.flush()
    # 创建者自动成为管理员
    db.add(Membership(project_id=project.id, user_id=owner.id, role=Role.admin))
    audit.record(
        db,
        action="project.create",
        user_id=owner.id,
        project_id=project.id,
        target_type="project",
        target_id=project.id,
    )
    db.flush()
    return project


def list_my_projects(db: Session, user_id: uuid.UUID, deleted=False) -> list[Project]:
    rows = db.execute(
        select(Project)
        .join(Membership, Membership.project_id == Project.id)
        .where(
            Membership.user_id == user_id,
            Project.deleted_at.is_not(None) if deleted else Project.deleted_at.is_(None),
            Membership.role == Role.admin if deleted else True,
        )
    ).scalars()
    return list(rows)


def set_deleted(db: Session, actor: User, project_id: uuid.UUID, deleted: bool) -> Project:
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    member = db.scalar(
        select(Membership).where(
            Membership.project_id == project_id, Membership.user_id == actor.id
        )
    )
    if project is None or member is None:
        raise Forbidden("项目不存在或无权访问")
    require(Role(member.role), "project.manage")
    if bool(project.deleted_at) == deleted:
        return project
    project.deleted_at = datetime.now(UTC) if deleted else None
    audit.record(
        db,
        action="project.delete" if deleted else "project.restore",
        user_id=actor.id,
        project_id=project.id,
        target_type="project",
        target_id=project.id,
        detail={"mode": "recycle_bin"},
    )
    db.flush()
    return project


def add_member(db: Session, project: Project, actor: User, data: schemas.MemberIn) -> Membership:
    lock_members(db, project, actor)
    user = (
        db.get(User, data.user_id)
        if data.user_id
        else db.scalar(select(User).where(User.username == data.username))
    )
    if user is None or not user.is_active:
        raise NotFound("目标用户不存在")
    existing = db.scalar(
        select(Membership).where(Membership.project_id == project.id, Membership.user_id == user.id)
    )
    if existing:
        if data.username:
            raise Conflict("该用户已是项目成员，请在成员列表调整角色")
        protect_member(project, actor, existing, data.role)
        existing.role = data.role
        membership = existing
    else:
        membership = Membership(project_id=project.id, user_id=user.id, role=data.role)
        db.add(membership)
    audit.record(
        db,
        action="member.upsert",
        user_id=actor.id,
        project_id=project.id,
        target_type="user",
        target_id=user.id,
        detail={"role": data.role.value},
    )
    db.flush()
    return membership


def lock_members(db, project, actor):
    db.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    member = db.scalar(
        select(Membership)
        .where(Membership.project_id == project.id, Membership.user_id == actor.id)
        .execution_options(populate_existing=True)
    )
    if member is None or member.role != Role.admin:
        raise Forbidden("只有项目管理员可以管理成员")


def protect_member(project, actor, member, role=None):
    if member.user_id == project.owner_id and role != Role.admin:
        raise Forbidden("不能移除项目所有者或降低其权限")
    if member.user_id == actor.id and role != Role.admin:
        raise Forbidden("不能在此移除自己或降低自己的管理权限")


def change_member(db, project, actor, user_id, role=None):
    lock_members(db, project, actor)
    member = db.scalar(
        select(Membership)
        .where(Membership.project_id == project.id, Membership.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    if member is None:
        raise NotFound("成员不存在")
    protect_member(project, actor, member, role)
    if role is None:
        db.delete(member)
    else:
        member.role = role
    audit.record(
        db,
        action="member.remove" if role is None else "member.role",
        user_id=actor.id,
        project_id=project.id,
        target_type="user",
        target_id=user_id,
        detail={"role": role.value if role is not None else None},
    )
    db.flush()
    return member


def member_out(member, user, project):
    return {
        "id": member.id,
        "project_id": member.project_id,
        "user_id": member.user_id,
        "role": member.role,
        "username": user.username,
        "display_name": user.display_name,
        "avatar_data": user.avatar_data,
        "is_owner": user.id == project.owner_id,
    }


def list_members(db: Session, project_id: uuid.UUID) -> list[Membership]:
    return list(db.scalars(select(Membership).where(Membership.project_id == project_id)))
