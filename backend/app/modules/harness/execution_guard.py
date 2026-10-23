"""Optional application execution grant; tools remain callable without an agent."""

import uuid

from sqlalchemy import select

from app.core.errors import Conflict, Forbidden
from app.models.harness import ProductionRun, ProductionTask


def authorize(db, ctx, context):
    from app.modules.harness.service import now

    grant = context.input_context.get("execution", {})
    if not grant.get("run_id"):
        return
    try:
        run_id, task_id, token = (
            uuid.UUID(str(grant[key])) for key in ("run_id", "task_id", "lease_token")
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise Forbidden("任务执行凭据无效") from exc
    run = db.scalar(
        select(ProductionRun)
        .where(ProductionRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    task = db.get(ProductionTask, task_id, populate_existing=True)
    if (
        not run
        or not task
        or task.run_id != run.id
        or run.created_by != ctx.user.id
        or run.project_id != ctx.project.id
    ):
        raise Forbidden("任务执行凭据不属于当前用户和项目")
    if (
        run.status not in {"queued", "running"}
        or run.context.get("guidance_pending")
        or task.status != "running"
        or task.lease_token != token
        or not task.lease_expires_at
        or task.lease_expires_at <= now()
    ):
        raise Conflict("任务已暂停、收到新指导或执行租约失效，当前操作未开始")
