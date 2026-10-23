"""Agent-independent, permission-preserving tool invocation.

No review proposal is applied here. The caller owns persistence of receipts and
transaction boundaries; idempotency is delegated only to declared business keys.
In particular an invocation ID is a correlation ID, never an exactly-once claim.
"""

import logging
import uuid
from collections.abc import Iterable
from contextlib import nullcontext
from typing import Any, Literal

from fastapi.encoders import jsonable_encoder
from jsonschema import ValidationError as SchemaError
from jsonschema import validate
from pydantic import BaseModel, ConfigDict, Field

from app.core.deps import get_project_context
from app.core.errors import AppError, CapabilityUnsupported, Conflict, Forbidden
from app.core.permissions import require
from app.modules.assist.targets import OpError, get_adapter
from app.modules.tools import capabilities, registry
from app.modules.tools.capabilities import Effect
from app.modules.tools.contracts import ToolContext

logger = logging.getLogger(__name__)


class ToolFailure(BaseModel):
    code: str
    message: str
    category: Literal[
        "authorization", "input", "conflict", "capability", "budget", "provider", "internal"
    ]
    recovery: Literal[
        "correct_input", "refresh_context", "review_budget", "query_status", "inspect"
    ]
    retryable: bool = False


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invocation_id: str
    tool: str
    version: int | None = None
    contract_version: int = 1
    effect: Effect | None = None
    status: Literal["succeeded", "review_required", "failed"]
    data: Any = None
    error: ToolFailure | None = None
    # No tool receipt implies the overall creative task is complete or adopted.
    adopted: bool = False
    review: dict | None = None
    recovery: dict = Field(default_factory=dict)


def _failure(error: AppError) -> ToolFailure:
    category, recovery = {
        "UNAUTHORIZED": ("authorization", "refresh_context"),
        "FORBIDDEN": ("authorization", "refresh_context"),
        "NOT_FOUND": ("input", "refresh_context"),
        "VERSION_CONFLICT": ("conflict", "refresh_context"),
        "LOCKED": ("conflict", "refresh_context"),
        "CAPABILITY_UNSUPPORTED": ("capability", "correct_input"),
        "QUOTA_EXCEEDED": ("budget", "review_budget"),
        "PROVIDER_ERROR": ("provider", "query_status"),
    }.get(error.code, ("internal", "inspect"))
    return ToolFailure(code=error.code, message=error.message, category=category, recovery=recovery)


def invoke(
    db,
    ctx,
    context: ToolContext,
    name: str,
    arguments: dict,
    *,
    expected_version: int | None = None,
    allowed_effects: Iterable[Effect] | None = None,
    invocation_id: str | None = None,
    allow_charges: bool = False,
) -> ToolResult:
    """Validate scope/effects and execute once using existing business permission checks.

    A review-required return is a proposal only; callers must use their explicit
    approval flow. Failed/uncertain calls must not be blindly replayed. This API
    cannot cancel external work; returned job references use existing job APIs.
    """
    result = ToolResult(
        invocation_id=invocation_id or str(uuid.uuid4()),
        tool=name,
        status="failed",
    )
    try:
        if not isinstance(context, ToolContext):
            raise CapabilityUnsupported("工具调用需要独立 ToolContext 上下文")
        if not isinstance(arguments, dict):
            raise CapabilityUnsupported("工具参数必须是对象")
        # Background calls must recheck membership: saved role/context is not a grant.
        live_ctx = get_project_context(project_id=ctx.project.id, user=ctx.user, db=db)
        capability = capabilities.get_capability(name, ctx=live_ctx, context=context)
        result.version, result.effect = capability.version, capability.effect
        if expected_version is not None and capability.version != expected_version:
            raise Conflict("工具版本已变化，请重新检查计划和参数")
        if capability.policy:
            for permission in capability.policy.permissions:
                require(live_ctx.role, permission)
        if not capability.available:
            raise CapabilityUnsupported(capability.unavailable_reason)
        allowed = (
            set(allowed_effects)
            if allowed_effects is not None
            else {"read", "candidate", "generate", "preview"}
        )
        if capability.effect not in allowed or capability.effect == "adopt":
            raise Forbidden("此任务没有授权该类工具副作用")
        if capability.effect != "read" and context.input_context.get("execution", {}).get("run_id"):
            from app.modules.harness.execution_guard import authorize

            # Serialize the write boundary with application control events.
            # Job dispatchers commit the intent before external work begins.
            authorize(db, live_ctx, context)
        tool, parsed = registry.parse(name, arguments)
        policy = capability.policy
        if policy.charges_on_invoke and not allow_charges:
            raise Forbidden("计费工具需要任务执行授权与预算预留")
        if policy.scope == "target":
            registry.scoped(context, parsed)
            if name == "object.propose_edit":
                require(live_ctx.role, get_adapter(parsed.target_type).edit_action)
        if policy.scope == "work" and str(parsed.work_id) != str(context.work_id):
            raise Forbidden("工具目标不属于任务关联的作品")
        if policy.idempotency == "request_key" and not getattr(parsed, "request_key", None):
            raise CapabilityUnsupported("写入候选需要稳定的 request_key")
        result.recovery = {
            "strategy": policy.recovery,
            "idempotency": policy.idempotency,
            "cancellation": policy.cancellation,
            "request_key": str(parsed.request_key) if hasattr(parsed, "request_key") else None,
        }
        # Synchronous DB proposals are atomic without rolling back caller-owned work.
        # Job dispatchers may commit their own durable job before external execution;
        # a savepoint cannot enclose those handlers. Their failures require inspection.
        transaction = nullcontext() if policy.starts_job else db.begin_nested()
        with transaction:
            output = jsonable_encoder(registry.execute(db, live_ctx, context, name, arguments))
            validate(output, capability.output_schema)
        result.data = output
        result.status = "review_required" if tool.review else "succeeded"
        if tool.review:
            result.review = {
                "tool": name,
                "version": tool.version,
                "arguments": parsed.model_dump(mode="json"),
                "result": output,
            }
        if isinstance(output, dict) and output.get("preview_job_id"):
            result.recovery["job_id"] = str(output["preview_job_id"])
    except AppError as exc:
        result.error = _failure(exc)
    except (OpError, ValueError, TypeError) as exc:
        # Malformed JSON/arguments are safe to correct, not automatic retry candidates.
        result.error = ToolFailure(
            code="INVALID_TOOL_INPUT",
            message=str(exc)[:800],
            category="input",
            recovery="correct_input",
        )
    except SchemaError:
        logger.exception("Tool %s returned an invalid result contract", name)
        result.error = ToolFailure(
            code="INVALID_TOOL_OUTPUT",
            message="工具返回不符合输出契约，请检查执行记录后恢复",
            category="internal",
            recovery="inspect",
        )
    except Exception:
        logger.exception("Tool invocation %s failed: %s", result.invocation_id, name)
        result.error = ToolFailure(
            code="TOOL_EXECUTION_FAILED",
            message="工具执行未确认完成，请检查状态后恢复",
            category="internal",
            recovery="inspect",
        )
    return result


def prepare_generation(db, ctx, context: ToolContext, arguments: dict, **options) -> ToolResult:
    """Prepare the existing business estimate/review; never dispatch or charge."""
    return invoke(db, ctx, context, "generation.request", arguments, **options)
