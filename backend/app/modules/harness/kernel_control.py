"""Explicit kernel replacement on a paused application-owned production graph.

Switching changes future decisions only. Project data, completed tasks, tool
receipts, budgets and approval grants remain owned by the production system.
"""

import uuid

from pydantic import Field, field_validator
from sqlalchemy import select

from app.core.errors import CapabilityUnsupported, Conflict, Forbidden
from app.core.permissions import require
from app.models.harness import ProductionEvent
from app.modules.harness import kernel, schemas, service
from app.modules.harness.context import revalidate_context


class SwitchKernelRequest(schemas.Strict):
    engine: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    provider: str | None = Field(default=None, min_length=1, max_length=120)
    expected_revision: int = Field(ge=1)
    request_key: uuid.UUID
    protocol_version: int = Field(default=1, ge=1)

    @field_validator("provider")
    @classmethod
    def nonblank_provider(cls, value):
        if value is not None and not value.strip():
            raise ValueError("模型配置名称不能为空")
        return value


def switch(db, ctx, run_id, request: SwitchKernelRequest | dict):
    data = SwitchKernelRequest.model_validate(request)
    live = revalidate_context(db, ctx)
    require(live.role, "agent.run")
    db.flush()
    run = service.get_run(db, live, run_id, lock=True)
    if run.created_by != live.user.id:
        raise Forbidden("只能切换自己发起任务的执行内核")

    payload = data.model_dump(mode="json")
    prior = db.scalar(
        select(ProductionEvent).where(
            ProductionEvent.run_id == run.id,
            ProductionEvent.request_key == data.request_key,
        )
    )
    if prior:
        if prior.kind != "user.switch_kernel" or prior.payload.get("request") != payload:
            raise Conflict("同一请求标识不能用于不同内核切换")
        return {**service.out(db, run), "model_provider": run.model_provider}
    if run.revision != data.expected_revision:
        raise Conflict("任务状态已更新，请刷新后再切换", {"revision": run.revision})
    if run.status != "paused":
        raise Conflict("请先暂停任务，待当前步骤结束后再切换内核")
    rows = service.tasks(db, run)
    if any(t.status == "running" or t.lease_token is not None for t in rows):
        # An expired lease is still unresolved work; do not implicitly reconcile or retry it.
        raise Conflict("仍有正在执行或待核对的步骤，请先等待或核对执行结果")

    adapter = kernel.get_kernel(data.engine)
    if (
        data.protocol_version != kernel.PROTOCOL_VERSION
        or getattr(adapter, "protocol_version", kernel.PROTOCOL_VERSION) != kernel.PROTOCOL_VERSION
    ):
        raise CapabilityUnsupported("执行内核与当前工程协议不兼容")
    if data.engine == "cloud_llm":
        from app.modules.assist.service import _engine

        configured = _engine(db, run.project_id, "cloud_llm", data.provider, user_id=live.user.id)
        if not all(
            isinstance(value, str) and value.strip()
            for value in (configured.base_url, configured.api_key, configured.model)
        ):
            raise CapabilityUnsupported("文字模型尚未完整配置，请检查服务地址、凭据和模型名称")
        # Configuration is resolved for validation only. Never persist decrypted credentials.
    elif data.provider is not None:
        raise CapabilityUnsupported("此内核不使用文字模型配置，请不要指定模型服务")

    previous = {"engine": run.engine, "provider": run.model_provider}
    current = {"engine": data.engine, "provider": data.provider}
    run.context = {
        key: value
        for key, value in run.context.items()
        if key not in {"kernel_binding", "kernel_profile"}
    }
    discarded = []
    for task in rows:
        if (
            task.kind == "agent"
            and task.status not in service.TERMINAL
            and "pending_action" in task.state
        ):
            task.state = {
                key: value for key, value in task.state.items() if key != "pending_action"
            }
            discarded.append(str(task.id))
    # Keep tasks and their original revisions intact: a switch must never replay completed work.
    run.engine, run.model_provider = data.engine, data.provider
    run.revision += 1
    service.event(
        db,
        run,
        "user.switch_kernel",
        {
            "request": payload,
            "previous": previous,
            "current": current,
            "protocol_version": kernel.PROTOCOL_VERSION,
            "discarded_pending_action_tasks": discarded,
        },
        actor_id=live.user.id,
        request_key=data.request_key,
    )
    db.flush()
    return {**service.out(db, run), "model_provider": run.model_provider}
