"""Platform console API. Explicit DTOs keep creative content and credentials private."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core import audit, health
from app.core.config import settings
from app.core.database import get_db
from app.core.errors import Conflict, NotFound
from app.core.platform_permissions import platform_admin, role, super_admin
from app.models.attachment import Attachment as ConversationAttachment
from app.models.generation import GenerationJob, Quota
from app.models.identity import AuditLog, Membership, Project, User, UserIdentity
from app.models.library import MediaVersion
from app.models.storage import Blob
from app.models.work import CreativeWork
from app.modules.admin import accounts, tasks

router = APIRouter(prefix="/admin", tags=["platform-admin"])
DB = Depends(get_db, scope="function")
ADMIN = Depends(platform_admin)
SUPER = Depends(super_admin)
TaskKind = Literal[
    "generation",
    "production",
    "upload",
    "attachment",
    "index",
    "processing",
    "transcription",
    "archive",
]


@router.get("/session")
def session(user: User = ADMIN):
    return {"role": role(user), "can_manage_accounts": role(user) == "super"}


def count(db, model, *filters):
    return db.scalar(select(func.count()).select_from(model).where(*filters)) or 0


@router.get("/overview")
def overview(db: Session = DB, user: User = ADMIN):
    now = datetime.now(UTC)
    since = now - timedelta(days=7)
    t = tasks.table()
    states = dict(db.execute(select(t.c.status, func.count()).group_by(t.c.status)).all())
    cost = db.execute(
        select(
            func.sum(GenerationJob.estimated_cost),
            func.sum(GenerationJob.actual_cost),
            func.count().filter(GenerationJob.actual_cost.is_(None)),
        ).where(GenerationJob.created_at >= since)
    ).one()
    return {
        "as_of": now,
        "users": count(db, User),
        "new_users_7d": count(db, User, User.created_at >= since),
        "active_users_7d": count(db, User, User.last_login_at >= since),
        "projects": count(db, Project, Project.deleted_at.is_(None)),
        "active_tasks": sum(states.get(s, 0) for s in tasks.ACTIVE),
        "attention_tasks": sum(states.get(s, 0) for s in tasks.ATTENTION),
        "storage_bytes": db.scalar(select(func.coalesce(func.sum(Blob.size_bytes), 0))),
        "usage_7d": {
            "estimated": cost[0] or 0,
            "recorded_actual": cost[1] or 0,
            "unknown_jobs": cost[2],
        },
        "recent_attention": tasks.listing(db, state="attention", limit=5)["items"],
    }


@router.get("/users")
def users(
    search: str = Query("", max_length=120),
    status: Literal["all", "active", "disabled"] = "all",
    account_role: Literal["all", "user", "admin", "super"] = "all",
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = DB,
    actor: User = ADMIN,
):
    filters = []
    if search.strip():
        filters.append(
            or_(
                *(
                    c.icontains(search.strip(), autoescape=True)
                    for c in (User.username, User.display_name, User.email)
                )
            )
        )
    if status != "all":
        filters.append(User.is_active == (status == "active"))
    if account_role == "user":
        filters.append(User.is_platform_admin.is_(False))
    elif account_role == "admin":
        filters.extend([User.is_platform_admin.is_(True), User.platform_role == "admin"])
    elif account_role == "super":
        filters.extend(
            [
                User.is_platform_admin.is_(True),
                or_(User.platform_role == "super", User.platform_role.is_(None)),
            ]
        )
    rows = db.scalars(
        select(User)
        .where(*filters)
        .order_by(User.created_at.desc(), User.id)
        .offset(offset)
        .limit(limit)
    )
    return {"items": [accounts.out(u) for u in rows], "total": count(db, User, *filters)}


@router.get("/users/{user_id}")
def user_detail(user_id: uuid.UUID, db: Session = DB, actor: User = ADMIN):
    user = db.get(User, user_id)
    if not user:
        raise NotFound("用户不存在")
    costs = db.execute(
        select(
            func.count(),
            func.sum(GenerationJob.estimated_cost),
            func.sum(GenerationJob.actual_cost),
            func.count().filter(GenerationJob.actual_cost.is_(None)),
        ).where(GenerationJob.created_by == user_id)
    ).one()
    return {
        **accounts.out(user),
        "login_methods": (["password"] if user.password_hash else [])
        + list(
            db.scalars(
                select(UserIdentity.provider).where(UserIdentity.user_id == user_id).distinct()
            )
        ),
        "owned_projects": count(
            db, Project, Project.owner_id == user_id, Project.deleted_at.is_(None)
        ),
        "memberships": count(db, Membership, Membership.user_id == user_id),
        "works_created": count(db, CreativeWork, CreativeWork.created_by == user_id),
        "generation_usage": {
            "jobs": costs[0],
            "estimated": costs[1] or 0,
            "recorded_actual": costs[2] or 0,
            "unknown_jobs": costs[3],
        },
    }


@router.post("/users/{user_id}/actions")
def user_action(
    user_id: uuid.UUID, data: accounts.AccountChange, db: Session = DB, actor: User = SUPER
):
    return accounts.out(accounts.change(db, actor.id, user_id, data))


@router.get("/tasks")
def task_list(
    kind: TaskKind | None = None,
    state: str = Query("", max_length=32),
    search: str = Query("", max_length=64),
    user_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = DB,
    actor: User = ADMIN,
):
    return tasks.listing(
        db,
        kind=kind or "",
        state=state,
        search=search,
        user_id=user_id,
        project_id=project_id,
        offset=offset,
        limit=limit,
    )


@router.get("/tasks/{kind}/{task_id}")
def task_detail(kind: TaskKind, task_id: uuid.UUID, db: Session = DB, actor: User = ADMIN):
    return tasks.detail(db, actor, kind, task_id)


def quota_out(q):
    token = hashlib.sha256(f"{q.id}:{q.limit_cost}:{q.period}:{q.updated_at}".encode()).hexdigest()
    return {
        "id": q.id,
        "project_id": q.project_id,
        "user_id": q.user_id,
        "scope": q.scope,
        "limit": q.limit_cost,
        "used": q.used_cost,
        "period": q.period,
        "version": token,
    }


@router.get("/quotas")
def quotas(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    user_id: uuid.UUID | None = None,
    db: Session = DB,
    actor: User = ADMIN,
):
    conditions = [Quota.user_id == user_id] if user_id else []
    rows = db.scalars(
        select(Quota)
        .where(*conditions)
        .order_by(Quota.created_at.desc(), Quota.id)
        .offset(offset)
        .limit(limit)
    )
    return {
        "items": tasks.describe(db, [quota_out(q) for q in rows]),
        "total": count(db, Quota, *conditions),
    }


class QuotaChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: str = Field(min_length=64, max_length=64)
    limit: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    reason: str = Field(min_length=1, max_length=300)


@router.put("/quotas/{quota_id}")
def change_quota(quota_id: uuid.UUID, data: QuotaChange, db: Session = DB, actor: User = SUPER):
    q = db.scalar(
        select(Quota)
        .where(Quota.id == quota_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not q:
        raise NotFound("配额不存在")
    if quota_out(q)["version"] != data.expected_version:
        raise Conflict("额度已被修改，请刷新后重试")
    if not data.reason.strip() or data.limit < q.used_cost:
        raise Conflict("请填写原因，额度不能小于已使用额度")
    before = str(q.limit_cost)
    q.limit_cost = data.limit
    audit.record(
        db,
        user_id=actor.id,
        action="admin.quota.update",
        target_type="quota",
        target_id=q.id,
        detail={"before": before, "after": str(data.limit), "reason": data.reason.strip()},
    )
    db.flush()
    return quota_out(q)


@router.get("/audit")
def audit_list(
    action: str = Query("", max_length=64),
    user_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = DB,
    actor: User = ADMIN,
):
    # Global records only. Project logs may contain private creative content.
    filters = [AuditLog.project_id.is_(None)]
    if action:
        filters.append(AuditLog.action.icontains(action, autoescape=True))
    if user_id:
        filters.append(AuditLog.user_id == user_id)
    rows = list(
        db.scalars(
            select(AuditLog)
            .where(*filters)
            .order_by(AuditLog.created_at.desc(), AuditLog.id)
            .offset(offset)
            .limit(limit)
        )
    )
    targets = {
        str(u.id): u.display_name or u.username
        for u in db.scalars(
            select(User).where(
                User.id.in_([r.target_id for r in rows if r.target_type == "user" and r.target_id])
            )
        )
    }
    return {
        "total": count(db, AuditLog, *filters),
        "items": tasks.describe(
            db,
            [
                {
                    "id": r.id,
                    "user_id": r.user_id,
                    "action": r.action,
                    "target_type": r.target_type,
                    "target_name": targets.get(str(r.target_id))
                    if r.target_type == "user"
                    else None,
                    "target_id": r.target_id,
                    "created_at": r.created_at,
                    "reason": r.detail.get("reason") if r.action.startswith("admin.") else None,
                }
                for r in rows
            ],
        ),
    }


@router.get("/system")
def system(db: Session = DB, actor: User = ADMIN):
    return {
        "storage_backend": settings.storage_backend,
        "blob_count": count(db, Blob),
        "blob_bytes": db.scalar(select(func.coalesce(func.sum(Blob.size_bytes), 0))),
        "uploads_incomplete": count(db, MediaVersion, MediaVersion.status == "uploading"),
        "attachments_incomplete": count(
            db, ConversationAttachment, ConversationAttachment.status == "uploading"
        ),
        "services": [
            {"name": name, "enabled": enabled, "configured": configured}
            for name, enabled, configured in [
                ("素材索引", settings.library_worker_enabled, bool(settings.library_indexer_url)),
                (
                    "媒体处理",
                    settings.library_processing_enabled,
                    bool(settings.library_wise_url or settings.library_mediacms_url),
                ),
                (
                    "音视频转写",
                    settings.transcription_worker_enabled,
                    bool(settings.transcriber_url),
                ),
                ("Agent 制作", settings.agent_worker_enabled, settings.production_harness_enabled),
            ]
        ],
        "retention": {
            "gc_enabled": settings.storage_gc_enabled,
            "archive_days": settings.archive_retention_days,
            "attachment_draft_days": settings.attachment_draft_ttl_days,
        },
    }


@router.post("/system/check")
def check_system(db: Session = DB, actor: User = ADMIN):
    result = health.readiness()
    audit.record(
        db, user_id=actor.id, action="admin.system.check", detail={"status": result["status"]}
    )
    return result
