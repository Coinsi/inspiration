"""审计:写操作 / 生成触发统一落 AuditLog。"""
import uuid

from sqlalchemy.orm import Session

from app.models.identity import AuditLog


def record(
    db: Session,
    *,
    action: str,
    user_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    detail: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            action=action,
            user_id=user_id,
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            detail=detail or {},
        )
    )
