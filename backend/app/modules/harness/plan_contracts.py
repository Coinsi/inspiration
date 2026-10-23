"""Read-only plan preflight and immutable executable capability snapshots.

Call after execution authorization (including budget changes), before scheduling
an approved plan. Draft plans may be stored before these execution prerequisites
are satisfied. Preflight never invokes a tool handler or starts a provider job.
"""

import hashlib
import json
import uuid
from decimal import Decimal

from app.core.errors import CapabilityUnsupported, Conflict, Forbidden, NotFound, QuotaExceeded
from app.core.permissions import require
from app.models.generation import GenerationJob
from app.modules.harness.context import resolve_reference, revalidate_context
from app.modules.tools import capabilities, registry
from app.modules.tools.contracts import ToolContext

CONTRACT_VERSION = 1
_PREFLIGHTS = {}


def register_preflight(name, validator):
    """Trusted plugins can add pure input checks without coupling to an agent kernel."""
    if name not in registry.CATALOG or name in _PREFLIGHTS or not callable(validator):
        raise ValueError("register a tool before its unique read-only preflight")
    _PREFLIGHTS[name] = validator


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def snapshot(capability):
    policy = capability.policy.model_dump(mode="json") if capability.policy else None
    return {
        "version": capability.version,
        "contract_version": capability.contract_version,
        "input_schema_hash": _digest(capability.input_schema),
        "output_schema_hash": _digest(capability.output_schema),
        "effect": capability.effect,
        "policy": policy,
        "requires_review": capability.requires_review,
    }


def assert_frozen_tool(name, frozen, *, ctx=None, context=None):
    """Reject version *and* undeclared schema/effect drift before executing an action."""
    if frozen.get("contract_version") != CONTRACT_VERSION:
        raise Conflict("任务工具契约版本不兼容，请重新检查计划")
    expected = frozen.get("tool_contracts", {}).get(name)
    if not expected:
        raise Conflict("工具尚未在此任务中读取并固定契约")
    capability = capabilities.get_capability(name, ctx=ctx, context=context)
    if snapshot(capability) != expected:
        raise Conflict("工具版本或执行契约已变化，请重新确认计划", {"tool": name})
    if not capability.available:
        raise CapabilityUnsupported(capability.unavailable_reason)
    return capability


def task_grants(run, task, parent=None):
    """Omission inherits; explicit lists only narrow the application grant."""
    spec = task.model_dump(mode="json") if hasattr(task, "model_dump") else task
    inherited = parent.specification if parent is not None else {}
    scope = inherited.get("scope")
    if scope is None:
        scope = run.context.get("scope", [])
    allowed = inherited.get("allowed_tools")
    selected = spec.get("scope") if spec.get("scope") is not None else scope

    def key(item):
        return item["target_type"], str(item["target_id"])

    if not {key(s) for s in selected} <= {key(s) for s in scope}:
        raise Forbidden("子任务不能扩大创作对象范围")
    tools = spec.get("allowed_tools") if spec.get("allowed_tools") is not None else allowed
    if allowed is not None and not set(tools or []) <= set(allowed):
        raise Forbidden("子任务不能扩大工具范围")
    requested = [*spec.get("tools", []), *([spec["tool"]] if spec.get("tool") else [])]
    if tools is not None and not set(requested) <= set(tools):
        raise Forbidden("任务包含不在其授权范围内的工具")
    return {"scope": selected, "allowed_tools": tools}


def tool_context(run, grants=None):
    return ToolContext(
        scope=(grants or {}).get("scope", run.context.get("scope", [])),
        skill_snapshot=run.context.get("skills", []),
        work_id=run.work_id,
        input_context={
            "conversation_id": str(run.conversation_id) if run.conversation_id else None
        },
    )


def _capability(ctx, context, run, name):
    capability = capabilities.get_capability(name, ctx=ctx, context=context)
    policy = capability.policy
    if policy:
        for permission in policy.permissions:
            require(ctx.role, permission)
    if not capability.available:
        raise CapabilityUnsupported(capability.unavailable_reason)
    if capability.effect not in run.policy.get("effects", []) or capability.effect == "adopt":
        raise Forbidden("制作计划包含尚未授权的操作", {"tool": name})
    if policy.charges_on_invoke and Decimal(str(run.policy.get("budget_points", 0))) <= 0:
        raise QuotaExceeded("制作计划包含计费操作，请先设置执行预算")
    return capability


def _scoped_arguments(db, ctx, context, capability, parsed):
    policy = capability.policy
    if policy.scope == "target":
        registry.scoped(context, parsed)
        from app.modules.assist.targets import get_adapter

        adapter = get_adapter(parsed.target_type)
        adapter.read(db, ctx.project.id, parsed.target_id)
        if capability.effect == "candidate":
            require(ctx.role, adapter.edit_action)
    if policy.scope == "work":
        if str(parsed.work_id) != str(context.work_id):
            raise Forbidden("计划的修改目标不属于关联作品")
        from app.modules.engineering.service import work

        work(db, ctx, parsed.work_id)
    if policy.scope == "selected_skill":
        selected = next((s for s in context.skill_snapshot if s["id"] == str(parsed.id)), None)
        if not selected:
            raise Forbidden("计划引用的技能未被本次任务选定")
    if policy.idempotency == "request_key" and not getattr(parsed, "request_key", None):
        raise CapabilityUnsupported("写入候选需要固定请求标识")


def _preview(db, ctx, context, args):
    from app.modules.engineering import executors, review
    from app.modules.engineering import service as engineering

    change = review.get_change(db, args.work_id, args.change_id)
    if change.fingerprint != args.fingerprint:
        raise Conflict("候选已变化，请重新读取预览目标")
    if change.status != "proposed":
        raise Conflict("预览计划必须引用待审候选")
    engineering.validate_sources(db, ctx, args.work_id, change.snapshot)
    updates = review.timeline_updates(change)
    selected = next(
        (
            u
            for u in updates
            if args.output_id
            in (review.timeline_output_id(change, u), "timeline:" + u["timeline_id"])
        ),
        None,
    )
    if selected:
        if selected.get("document_ref"):
            from app.modules.engineering.documents import compile_sequence

            compile_sequence(db, args.work_id, selected["document_ref"])
        return {
            "change_id": str(change.id),
            "output_id": review.timeline_output_id(change, selected),
        }
    roots = change.snapshot.get("native_roots", {})
    output = args.output_id or (next(iter(roots)) if len(roots) == 1 else None)
    if output not in roots:
        raise CapabilityUnsupported("请选择需要预览的工程输出")
    plan, output_type = executors.compile_plan(db, args.work_id, roots[output])
    return {
        "change_id": str(change.id),
        "output_id": output,
        "output_type": output_type,
        "executor": plan.get("executor"),
    }


def _preflight(db, ctx, context, name, parsed):
    if name in _PREFLIGHTS:
        return _PREFLIGHTS[name](db, ctx, context, parsed)
    if name in {"media.generate", "media.estimate"}:
        from app.modules.harness.media_tools import estimate

        return estimate(db, ctx, context, parsed)
    if name == "media.inspect":
        from app.modules.harness.media_context import describe_media

        if parsed.metadata_only and (parsed.frame_times_ms or parsed.include_audio):
            raise CapabilityUnsupported("仅元数据检查不能包含抽帧或试听")
        return {"reference": describe_media(db, ctx, parsed.reference)["reference"]}
    if name in {"media.job_observe", "job.read"}:
        from app.modules.generation.service import get_job

        get_job(db, ctx.project.id, getattr(parsed, "job_id", None) or parsed.id)
    if name == "engineering.preview":
        return _preview(db, ctx, context, parsed)
    if name == "engineering.compose":
        from app.modules.harness import editing_tools

        check = getattr(editing_tools, "preflight", None)
        if check:
            return check(db, ctx, context, parsed)
        return _compose(db, ctx, parsed)
    return {}


def _compose(db, ctx, args):
    """Resolve recipe media without allocating a timeline, link, or candidate."""
    from app.modules.engineering import service as engineering
    from app.modules.harness.media_context import describe_media

    base = engineering.snapshot_at(db, args.work_id, args.base_revision)
    if args.document_id:
        root = base.get("native_roots", {}).get(str(args.document_id))
        if (root["revision"] if root else 0) != args.document_revision:
            raise Conflict("配方目标文档与固定工程基线不一致")
    recipe = args.recipe
    if recipe.mode == "sequence":
        require(ctx.role, "timeline.edit")
        if recipe.timeline_id:
            from app.models.timeline import Timeline
            from app.modules.conversation.works import timeline_ids

            if recipe.timeline_id not in timeline_ids(db, args.work_id):
                raise Forbidden("计划的目标剪辑不属于关联作品")
            row = db.get(Timeline, recipe.timeline_id)
            if not row or row.revision != recipe.timeline_revision:
                raise Conflict("计划中的剪辑基线已变化")
    refs = []

    def source(item, allowed, *, start=0, duration=None):
        info = describe_media(db, ctx, item.model_dump(mode="json"))
        if info["media_type"] not in allowed:
            raise CapabilityUnsupported("配方中的素材类型不适用于此位置")
        if info["media_type"] == "image":
            if start:
                raise CapabilityUnsupported("静态图片的源起点必须为零")
        elif duration is not None:
            length = info["metadata"].get("duration_ms")
            if length is None or start + duration > length:
                raise CapabilityUnsupported("配方缺少有效源时长或超出固定素材区间")
        refs.append(info["reference"])

    if recipe.mode == "image":
        for layer in recipe.layers:
            source(layer.source, {"image"})
            if layer.mask:
                source(layer.mask, {"image"})
    elif recipe.mode == "audio":
        for track in recipe.tracks:
            for clip in track.clips:
                source(
                    clip.source, {"audio"}, start=clip.source_start_ms, duration=clip.duration_ms
                )
    else:
        for clip in [*recipe.clips, *recipe.overlays]:
            source(
                clip.source,
                {"image", "video"},
                start=clip.source_start_ms,
                duration=clip.duration_ms,
            )
        for clip in recipe.audio:
            source(clip.source, {"audio"}, start=clip.source_start_ms, duration=clip.duration_ms)
    return {
        "references": refs,
        "base_revision": args.base_revision,
        "deep_recipe_preflight": "execution_time",
        "note": "固定素材、类型、区间和目标基线已检查；完整配方与执行器兼容性在候选执行前再次校验",
    }


def _reserved(db, run):
    total = Decimal(0)
    for reservation in run.context.get("budget_reservations", {}).values():
        job = db.get(GenerationJob, uuid.UUID(reservation["job_id"]))
        if not job or job.project_id != run.project_id:
            raise Conflict("运行预算记录不能核验，请先核对已有任务")
        total += Decimal(
            str(job.actual_cost if job.actual_cost is not None else reservation["estimated_points"])
        )
    return total


def validate_plan(db, ctx, run, plan, parent=None):
    """Return contracts by task key; mutate neither the run nor domain objects."""
    ctx = revalidate_context(db, ctx)
    if run.project_id != ctx.project.id or run.created_by != ctx.user.id:
        raise NotFound("制作计划不属于当前用户和项目")
    if parent is not None and parent.run_id != run.id:
        raise NotFound("父任务不属于当前制作运行")
    contracts, estimated_total = {}, Decimal(0)
    for task in plan.tasks:
        grants = task_grants(run, task, parent)
        context = tool_context(run, grants)
        names = list(dict.fromkeys([*task.tools, *([task.tool] if task.tool else [])]))
        available = {name: _capability(ctx, context, run, name) for name in names}
        references = [
            resolve_reference(db, ctx, ref)["reference"]
            for ref in (task.references or run.context.get("references", []))
        ]
        arguments, details = None, {}
        if task.kind == "tool":
            _, parsed = registry.parse(task.tool, task.arguments)
            _scoped_arguments(db, ctx, context, available[task.tool], parsed)
            arguments = parsed.model_dump(mode="json")
            details = _preflight(db, ctx, context, task.tool, parsed)
            if available[task.tool].policy.charges_on_invoke:
                if "estimated_points" not in details:
                    raise CapabilityUnsupported("计费工具缺少只读估价预检，不能直接执行计划")
                amount = Decimal(str(details["estimated_points"]))
                if not amount.is_finite() or amount < 0:
                    raise CapabilityUnsupported("工具返回的计划估价无效")
                estimated_total += amount
            if task.tool in {"media.generate", "media.estimate"}:
                for ref in parsed.references:
                    references.append(
                        resolve_reference(
                            db,
                            ctx,
                            {
                                "kind": "generation",
                                "id": str(ref.generation_id),
                                "purpose": ref.role,
                                "required": True,
                            },
                        )["reference"]
                    )
            if task.tool == "media.inspect":
                references.append(details["reference"])
            if task.tool == "engineering.compose":
                references.extend(details.get("references", []))
        frozen = {
            "grants": grants,
            "contract_version": CONTRACT_VERSION,
            "tool_versions": {name: cap.version for name, cap in available.items()},
            "tool_contracts": {name: snapshot(cap) for name, cap in available.items()},
            "references": list({_digest(ref): ref for ref in references}.values()),
            "arguments": arguments,
            "preflight": details,
        }
        frozen["fingerprint"] = _digest(frozen)
        contracts[task.key] = frozen
    if estimated_total and _reserved(db, run) + estimated_total > Decimal(
        str(run.policy.get("budget_points", 0))
    ):
        raise QuotaExceeded("计划估价与已有任务预留费用合计超过执行预算")
    return contracts
