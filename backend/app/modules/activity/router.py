import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context
from app.modules.activity import catalog, details

router = APIRouter(prefix="/projects/{project_id}/activity", tags=["activity"])


@router.get("")
def listing(
    status: Literal[
        "",
        "active",
        "attention",
        "pending",
        "submitted",
        "running",
        "paused",
        "needs_review",
        "failed",
        "succeeded",
        "canceled",
    ] = "",
    operation: str = Query("", max_length=40),
    search: str = Query("", max_length=200),
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(24, ge=1, le=100),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return catalog.catalog(db, ctx, status, operation, search, offset, limit)


@router.get("/{kind}/{task_id}")
def read(
    kind: str,
    task_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return details.detail(db, ctx, kind, task_id)
