"""Platform models have no project owner; management requires platform administrator rights."""

from types import SimpleNamespace

from fastapi import Depends
from sqlalchemy import select, text

from app.core.platform_permissions import platform_admin
from app.models.identity import Project


def lock_model_scope(db, project_id):
    if project_id is None:
        # Serialize platform defaults and idempotent first-time creates across administrators.
        db.execute(text("SELECT pg_advisory_xact_lock(18423, 56)"))
    else:
        db.scalar(select(Project).where(Project.id == project_id).with_for_update())


def platform_context(user=Depends(platform_admin)):
    return SimpleNamespace(user=user, project=SimpleNamespace(id=None))
