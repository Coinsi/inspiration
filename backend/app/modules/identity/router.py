"""identity 路由:认证、当前用户、项目、成员、审计。"""

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_current_user, get_project_context, require_action
from app.models.identity import AuditLog, User
from app.modules.identity import schemas, service
from app.modules.identity import preferences

router = APIRouter()


@router.get("/projects/{project_id}/creative-preferences", tags=["project"])
def creative_preferences(ctx: ProjectContext = Depends(get_project_context)):
    return preferences.read(ctx.project)


@router.put("/projects/{project_id}/creative-preferences", tags=["project"])
def save_creative_preferences(data: preferences.CreativePreferences,
    ctx: ProjectContext = Depends(require_action("project.manage")),
    db: Session = Depends(get_db, scope="function")):
    return preferences.save(db, ctx, data)


# ── 认证 ──
@router.post("/auth/register", response_model=schemas.UserOut, tags=["auth"])
def register(data: schemas.RegisterIn, db: Session = Depends(get_db, scope="function")) -> User:
    return service.register_user(db, data)


@router.post("/auth/login", response_model=schemas.TokenOut, tags=["auth"])
def login(
    data: schemas.LoginIn, db: Session = Depends(get_db, scope="function")
) -> schemas.TokenOut:
    return schemas.TokenOut(access_token=service.login(db, data))


@router.get("/me", response_model=schemas.MeOut, tags=["auth"])
def me(
    user: User = Depends(get_current_user), db: Session = Depends(get_db, scope="function")
) -> schemas.MeOut:
    memberships = service.get_memberships(db, user.id)
    return schemas.MeOut(
        user=schemas.UserOut.model_validate(user),
        memberships=[schemas.MemberOut.model_validate(m) for m in memberships],
    )


# ── 项目 ──
@router.post("/projects", response_model=schemas.ProjectOut, tags=["project"])
def create_project(
    data: schemas.ProjectIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
):
    return service.create_project(db, user, data)


@router.get("/projects", response_model=list[schemas.ProjectOut], tags=["project"])
def list_projects(
    deleted: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
):
    return service.list_my_projects(db, user.id, deleted)


@router.delete("/projects/{project_id}", status_code=204, tags=["project"])
def delete_project(
    project_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
):
    service.set_deleted(db, user, project_id, True)
    return Response(status_code=204)


@router.post("/projects/{project_id}/restore", response_model=schemas.ProjectOut, tags=["project"])
def restore_project(
    project_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
):
    return service.set_deleted(db, user, project_id, False)


@router.get("/projects/{project_id}", response_model=schemas.ProjectOut, tags=["project"])
def get_project(ctx: ProjectContext = Depends(get_project_context)):
    return ctx.project


# ── 成员 ──
@router.post("/projects/with-cover", response_model=schemas.ProjectOut, tags=["project"])
def create_with_cover(
    name: str = Form(..., min_length=1, max_length=255, pattern=r"\S"),
    request_key: uuid.UUID = Form(...),
    description: str = Form("", max_length=10000),
    file: UploadFile | None = File(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.identity import covers

    return covers.create(db, user, name, description, request_key, file)


@router.post("/projects/{project_id}/cover", response_model=schemas.ProjectOut, tags=["project"])
def upload_cover(
    file: UploadFile = File(...),
    ctx: ProjectContext = Depends(require_action("project.manage")),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.identity import covers

    return covers.change(db, ctx, file)


@router.delete("/projects/{project_id}/cover", response_model=schemas.ProjectOut, tags=["project"])
def remove_cover(
    ctx: ProjectContext = Depends(require_action("project.manage")),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.identity import covers

    return covers.change(db, ctx)


# ── 成员 ──
@router.post("/projects/{project_id}/members", response_model=schemas.MemberOut, tags=["member"])
def add_member(
    data: schemas.MemberIn,
    ctx: ProjectContext = Depends(require_action("member.manage")),
    db: Session = Depends(get_db, scope="function"),
):
    return service.add_member(db, ctx.project, ctx.user, data)


@router.get(
    "/projects/{project_id}/members", response_model=list[schemas.MemberOut], tags=["member"]
)
def list_members(
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.list_members(db, ctx.project.id)


# ── 审计 ──
@router.get(
    "/projects/{project_id}/audit-logs", response_model=list[schemas.AuditOut], tags=["audit"]
)
def audit_logs(
    ctx: ProjectContext = Depends(require_action("project.manage")),
    db: Session = Depends(get_db, scope="function"),
    limit: int = 100,
):
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.project_id == ctx.project.id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    return list(rows)
