"""identity 业务逻辑。"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import Conflict, NotFound, Unauthorized
from app.core.security import create_access_token, hash_password, verify_password
from app.models.enums import Role
from app.models.identity import Membership, Project, User
from app.modules.identity import schemas


def register_user(db: Session, data: schemas.RegisterIn) -> User:
    exists = db.scalar(select(User).where(User.username == data.username))
    if exists:
        raise Conflict("用户名已存在")
    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        display_name=data.display_name,
    )
    db.add(user)
    db.flush()
    return user


def login(db: Session, data: schemas.LoginIn) -> str:
    user = db.scalar(select(User).where(User.username == data.username))
    if user is None or not verify_password(data.password, user.password_hash):
        raise Unauthorized("用户名或密码错误")
    if not user.is_active:
        raise Unauthorized("用户已禁用")
    return create_access_token(str(user.id))


def get_memberships(db: Session, user_id: uuid.UUID) -> list[Membership]:
    return list(db.scalars(select(Membership).where(Membership.user_id == user_id)))


def create_project(db: Session, owner: User, data: schemas.ProjectIn) -> Project:
    if db.scalar(select(Project).where(Project.code == data.code)):
        raise Conflict("项目编码已存在")
    project = Project(
        code=data.code,
        name=data.name,
        description=data.description,
        owner_id=owner.id,
        settings=data.settings,
    )
    db.add(project)
    db.flush()
    # 创建者自动成为管理员
    db.add(Membership(project_id=project.id, user_id=owner.id, role=Role.admin))
    audit.record(db, action="project.create", user_id=owner.id, project_id=project.id,
                 target_type="project", target_id=project.id)
    db.flush()
    return project


def list_my_projects(db: Session, user_id: uuid.UUID) -> list[Project]:
    rows = db.execute(
        select(Project)
        .join(Membership, Membership.project_id == Project.id)
        .where(Membership.user_id == user_id, Project.deleted_at.is_(None))
    ).scalars()
    return list(rows)


def add_member(db: Session, project: Project, actor: User, data: schemas.MemberIn) -> Membership:
    if db.get(User, data.user_id) is None:
        raise NotFound("目标用户不存在")
    existing = db.scalar(
        select(Membership).where(
            Membership.project_id == project.id, Membership.user_id == data.user_id
        )
    )
    if existing:
        existing.role = data.role
        membership = existing
    else:
        membership = Membership(project_id=project.id, user_id=data.user_id, role=data.role)
        db.add(membership)
    audit.record(db, action="member.upsert", user_id=actor.id, project_id=project.id,
                 target_type="user", target_id=data.user_id, detail={"role": data.role.value})
    db.flush()
    return membership


def list_members(db: Session, project_id: uuid.UUID) -> list[Membership]:
    return list(db.scalars(select(Membership).where(Membership.project_id == project_id)))
