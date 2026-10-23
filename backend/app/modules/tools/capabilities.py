"""Versioned execution contracts for the shared tool registry.

The registry is authoritative for handlers and argument schemas. Contracts add
effect and recovery semantics; an unclassified extension is never assumed safe
or read-only. No agent runtime, prompt, or provider is required to use this API.
"""

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import CapabilityUnsupported
from app.core.permissions import PERMISSIONS, can
from app.modules.tools.contracts import ToolContext

Effect = Literal["read", "candidate", "generate", "preview", "adopt"]


class ToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    effect: Effect
    permissions: tuple[str, ...] = ()
    scope: Literal["project", "target", "work", "selected_skill", "conversation"] = "project"
    idempotency: Literal["read_only", "proposal_only", "request_key", "business_key", "none"]
    cancellation: Literal["not_applicable", "job_cooperative", "unsupported"]
    recovery: Literal["repeat_read", "repeat_proposal", "same_request", "query_job", "manual"]
    starts_job: bool = False
    charges_on_invoke: bool = False
    output_kind: str
    output_schema: dict = Field(default_factory=lambda: {"type": "object"})


class ToolCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: int
    contract_version: int = 1
    description: str
    group: str
    input_schema: dict
    output_schema: dict
    effect: Effect | None
    policy: ToolPolicy | None
    requires_review: bool
    available: bool
    unavailable_reason: str | None = None


def _read(output_kind, scope="project"):
    return ToolPolicy(
        effect="read",
        scope=scope,
        idempotency="read_only",
        cancellation="not_applicable",
        recovery="repeat_read",
        output_kind=output_kind,
    )


_POLICIES = {
    "project.overview": _read("project_summary"),
    "object.read": _read("object_state"),
    "library.search": _read("media_search_results"),
    "canvas.read": _read("canvas_document"),
    "job.read": _read("job_status"),
    "skill.load": _read("fixed_skill", "selected_skill"),
    "skill.read_file": _read("fixed_skill_file", "selected_skill"),
    "present": _read("presentation_receipt", "conversation"),
    # A protocol acknowledgement, not proof that a production task is complete.
    "finish": _read("completion_suggestion"),
    "object.propose_edit": ToolPolicy(
        effect="candidate",
        scope="target",
        idempotency="proposal_only",
        cancellation="not_applicable",
        recovery="repeat_proposal",
        output_kind="edit_proposal",
        output_schema={"type": "object", "required": ["before", "after", "expected"]},
    ),
    "generation.request": ToolPolicy(
        effect="generate",
        permissions=("generation.trigger",),
        scope="target",
        idempotency="proposal_only",
        cancellation="not_applicable",
        recovery="repeat_proposal",
        output_kind="generation_proposal",
        output_schema={"type": "object", "required": ["estimated_points", "expected"]},
    ),
    "engineering.read": _read("engineering_snapshot"),
    # This persists a draft even though Tool.review is False. It is never a read.
    "engineering.propose": ToolPolicy(
        effect="candidate",
        permissions=("engineering.edit",),
        scope="work",
        idempotency="request_key",
        cancellation="not_applicable",
        recovery="same_request",
        output_kind="engineering_candidate",
        output_schema={"type": "object", "required": ["id", "fingerprint", "status"]},
    ),
    "engineering.preview": ToolPolicy(
        effect="preview",
        permissions=("engineering.edit",),
        scope="work",
        idempotency="business_key",
        cancellation="job_cooperative",
        recovery="query_job",
        starts_job=True,
        output_kind="engineering_preview_job",
        output_schema={"type": "object", "required": ["id", "preview_job_id"]},
    ),
}


def register_contract(name: str, policy: ToolPolicy) -> None:
    """Application plugins declare semantics explicitly, after registering a tool."""
    from app.modules.tools import registry

    if name not in registry.CATALOG:
        raise ValueError("Register the tool handler before its execution contract")
    if name in _POLICIES:
        raise ValueError("Tool contract already registered")
    unknown_permissions = set(policy.permissions) - PERMISSIONS.keys()
    if unknown_permissions:
        raise ValueError("Tool contracts may only name registered permissions")
    if registry.CATALOG[name].review and policy.charges_on_invoke:
        raise ValueError("Review proposal handlers cannot charge on invocation")
    _POLICIES[name] = policy


def get_capability(name: str, *, ctx=None, context: ToolContext | None = None) -> ToolCapability:
    from app.modules.tools import registry

    tool = registry.CATALOG.get(name)
    if not tool:
        raise CapabilityUnsupported("未登记的工具，不能执行")
    policy = _POLICIES.get(name)
    reason = None
    if policy is None:
        reason = "工具尚未声明副作用和恢复契约"
    elif policy.effect == "adopt":
        reason = "正式采用需要独立的用户审阅入口"
    elif ctx is not None and any(not can(ctx.role, p) for p in policy.permissions):
        reason = "当前角色无权使用此工具"
    elif context is not None:
        if policy.scope == "work" and not context.work_id:
            reason = "请先关联作品"
        elif policy.scope == "target" and not context.scope:
            reason = "请先授权具体的创作对象"
        elif policy.scope == "selected_skill" and not context.skill_snapshot:
            reason = "没有为此任务选择技能"
        elif policy.scope == "conversation" and not context.input_context.get("conversation_id"):
            reason = "此工具需要创作对话上下文"
    return ToolCapability(
        name=name,
        version=tool.version,
        description=tool.description,
        group=tool.group,
        input_schema=tool.schema.model_json_schema(),
        output_schema=policy.output_schema if policy else {"type": "object"},
        effect=policy.effect if policy else None,
        policy=policy,
        requires_review=tool.review,
        available=reason is None,
        unavailable_reason=reason,
    )


def catalogue(
    *,
    names: Iterable[str] | None = None,
    group: str | None = None,
    effects: Iterable[Effect] | None = None,
    ctx=None,
    context: ToolContext | None = None,
    available_only: bool = False,
) -> list[dict]:
    """Discover just the requested tools; capability is rechecked on invocation.

    Availability here describes contract/scope/role readiness. Provider-specific
    input compatibility and quotas are checked by the business handler, not guessed.
    """
    from app.modules.tools import registry

    selected = set(names) if names is not None else None
    selected_effects = set(effects) if effects is not None else None
    result = []
    for name, tool in registry.CATALOG.items():
        if (
            selected is not None
            and name not in selected
            or group is not None
            and tool.group != group
        ):
            continue
        capability = get_capability(name, ctx=ctx, context=context)
        if selected_effects is not None and capability.effect not in selected_effects:
            continue
        if available_only and not capability.available:
            continue
        result.append(capability.model_dump(mode="json"))
    return result
