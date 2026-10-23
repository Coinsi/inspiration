"""Transactional production graph, controlled exclusively by the application.

Every writer locks the run before tasks. External I/O must happen outside this
transaction; completion is fenced by the task lease, never by a global run lock.
"""

import copy
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.errors import Conflict, Forbidden, NotFound
from app.core.permissions import require
from app.models.harness import ProductionAttempt, ProductionEvent, ProductionRun, ProductionTask
from app.models.identity import Project
from app.modules.agent.service import context as execution_context
from app.modules.harness import schemas

TERMINAL = {"succeeded", "failed", "canceled", "superseded"}


def now():
    return datetime.now(UTC)


def event(db, run, kind, payload=None, *, actor_id=None, request_key=None):
    run.event_sequence += 1
    item = ProductionEvent(
        run_id=run.id,
        sequence=run.event_sequence,
        kind=kind,
        actor_id=actor_id,
        request_key=request_key,
        payload=payload or {},
    )
    db.add(item)
    return item


def tasks(db, run):
    return list(
        db.scalars(
            select(ProductionTask)
            .where(ProductionTask.run_id == run.id)
            .order_by(ProductionTask.created_at, ProductionTask.key)
        )
    )


def get_run(db, ctx, run_id, lock=False):
    query = select(ProductionRun).where(
        ProductionRun.id == run_id, ProductionRun.project_id == ctx.project.id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    run = db.scalar(query)
    if not run or (run.conversation_id and run.created_by != ctx.user.id):
        raise NotFound("制作任务不存在")
    return run


def out(db, run, detail=True):
    from app.modules.harness.resources import summary as resource_summary

    value = {
        key: getattr(run, key)
        for key in (
            "id",
            "work_id",
            "conversation_id",
            "goal",
            "engine",
            "model_provider",
            "status",
            "revision",
            "event_sequence",
            "policy",
            "plan",
            "result",
            "error",
            "created_at",
            "updated_at",
        )
    }
    value["resources"] = resource_summary(run)
    if detail:
        value["tasks"] = [
            {
                key: getattr(t, key)
                for key in (
                    "id",
                    "parent_id",
                    "key",
                    "role",
                    "kind",
                    "goal",
                    "status",
                    "dependencies",
                    "revision",
                    "result",
                    "error",
                    "attempts",
                    "state",
                )
            }
            for t in tasks(db, run)
        ]
    return value


def add_plan(db, run, plan, parent=None):
    from app.modules.harness.plan_contracts import validate_plan

    contracts = validate_plan(db, execution_context(db, run), run, plan, parent)
    current = tasks(db, run)
    if len(current) + len(plan.tasks) > run.policy["max_tasks"]:
        raise Conflict("制作任务数量已达上限，请缩小计划")
    depth = 0
    ancestor = parent
    while ancestor:
        depth += 1
        ancestor = db.get(ProductionTask, ancestor.parent_id) if ancestor.parent_id else None
    if depth > run.policy["max_depth"]:
        raise Conflict("任务委派层级已达上限")
    prefix = f"{parent.id.hex[:10]}." if parent else ""
    mapping = {spec.key: uuid.uuid4() for spec in plan.tasks}
    for spec in plan.tasks:
        # No delegated task can increase its parent's effect or resource grant.
        row = ProductionTask(
            id=mapping[spec.key],
            run_id=run.id,
            parent_id=parent.id if parent else None,
            key=(prefix + spec.key)
            if len(prefix + spec.key) <= 80
            else prefix + spec.key[:48] + "." + mapping[spec.key].hex[:16],
            revision=run.revision,
            role=spec.role,
            kind=spec.kind,
            goal=spec.goal,
            specification={
                **spec.model_dump(mode="json"),
                **contracts[spec.key]["grants"],
                "references": contracts[spec.key]["references"],
                "arguments": contracts[spec.key]["arguments"] or spec.arguments,
                "contract": contracts[spec.key],
            },
            dependencies=[str(mapping[k]) for k in spec.dependencies],
            state={
                **(
                    {"plan_grant": parent.state["plan_grant"]}
                    if parent and parent.state.get("plan_grant")
                    else {}
                ),
                **(
                    {"read_preconditions": parent.state["read_preconditions"]}
                    if parent and parent.state.get("read_preconditions")
                    else {}
                ),
            },
            status="queued",
            result={},
            error={},
        )
        db.add(row)
    if parent:
        parent.status = "waiting_children"
        parent.state = {**parent.state, "children": [str(v) for v in mapping.values()]}
    db.flush()
    event(
        db,
        run,
        "plan.accepted",
        {"summary": plan.summary, "tasks": [str(v) for v in mapping.values()]},
    )


def create(db, ctx, data):
    require(ctx.role, "agent.run")
    db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    prior = db.scalar(
        select(ProductionRun).where(
            ProductionRun.project_id == ctx.project.id,
            ProductionRun.created_by == ctx.user.id,
            ProductionRun.request_key == data.request_key,
        )
    )
    if prior:
        if prior.context.get("request") != data.model_dump(mode="json"):
            raise Conflict("同一请求标识不能用于不同制作需求")
        return out(db, prior)
    from app.modules.harness.kernel import get_kernel

    get_kernel(data.engine)
    if data.work_id:
        from app.modules.conversation.works import get as get_work

        get_work(db, ctx.project.id, data.work_id)
    if data.conversation_id:
        from app.models.conversation import Conversation

        conversation = db.get(Conversation, data.conversation_id)
        if (
            not conversation
            or conversation.project_id != ctx.project.id
            or conversation.created_by != ctx.user.id
            or conversation.archived_at
        ):
            raise NotFound("对话不存在或已归档")
    from app.modules.assist.targets import get_adapter

    for target in data.scope:
        adapter = get_adapter(target.target_type)
        adapter.read(db, ctx.project.id, target.target_id)
        require(ctx.role, adapter.edit_action)
    from app.modules.harness.context import build_context, resolve_reference
    from app.modules.skill.service import selected_versions, snapshots

    # Resolve all fixed references now. Persist normalized refs, not signed URLs.
    build_context(db, ctx, goal=data.goal, references=data.references)
    pinned_references = [resolve_reference(db, ctx, ref)["reference"] for ref in data.references]
    if data.skill_versions and {s.id for s in data.skill_versions} != set(data.skills):
        raise Conflict("所选技能与固定版本不一致")
    skills = (
        selected_versions(db, ctx.project.id, data.skill_versions)
        if data.skill_versions
        else snapshots(db, ctx, data.skills)
    )
    run = ProductionRun(
        project_id=ctx.project.id,
        created_by=ctx.user.id,
        request_key=data.request_key,
        work_id=data.work_id,
        conversation_id=data.conversation_id,
        goal=data.goal,
        engine=data.engine,
        model_provider=data.model_provider,
        status="needs_plan_approval" if data.plan else "queued",
        revision=1,
        event_sequence=0,
        policy=data.policy.model_dump(mode="json"),
        context={
            "format_version": 1,
            "kernel_protocol_version": 1,
            "scope": [t.model_dump(mode="json") for t in data.scope],
            "references": pinned_references,
            "skills": skills,
            "facts": [],
            "history": [],
            "request": data.model_dump(mode="json"),
        },
        plan=data.plan.model_dump(mode="json") if data.plan else {},
        result={},
        error={},
    )
    db.add(run)
    db.flush()
    event(db, run, "run.created", {"goal": data.goal}, actor_id=ctx.user.id)
    if not data.plan:
        add_plan(
            db,
            run,
            schemas.Plan(
                summary="理解需求并准备制作方案",
                tasks=[schemas.TaskSpec(key="coordinator", goal=data.goal, role="coordinator")],
            ),
        )
    return out(db, run)


def affected_tasks(rows, selected):
    """Transitive consumers and delegated descendants; preserve unrelated work."""
    affected = set(selected)
    while True:
        more = {
            t.id
            for t in rows
            if t.parent_id in affected or any(uuid.UUID(dep) in affected for dep in t.dependencies)
        }
        if more <= affected:
            return affected
        affected |= more


def control(db, ctx, run_id, data):
    db.flush()
    run = get_run(db, ctx, run_id, True)
    require(ctx.role, "agent.run")
    if run.created_by != ctx.user.id:
        raise Forbidden("只能控制自己发起的制作任务")
    prior = db.scalar(
        select(ProductionEvent).where(
            ProductionEvent.run_id == run.id, ProductionEvent.request_key == data.request_key
        )
    )
    if prior:
        if prior.kind != f"user.{data.action}" or prior.payload.get("request") != data.model_dump(
            mode="json"
        ):
            raise Conflict("同一请求标识不能用于不同操作")
        return out(db, run)
    if data.expected_revision != run.revision:
        raise Conflict("任务状态已更新，请刷新后操作", {"revision": run.revision})
    execution_context(db, run)
    if run.context.get("guidance_pending") and data.action in {"steer", "reply"}:
        # An impact decision uses its system-derived apply key; other control
        # requests must go through the pending inbox so it cannot remain orphaned.
        if data.request_key != uuid.uuid5(uuid.UUID(run.context["guidance_pending"]), "apply"):
            raise Conflict("请先回复当前指导问题，新的意见将由影响评估一起处理")
    rows = tasks(db, run)
    was_paused = run.status == "paused"
    row_ids = {t.id for t in rows}
    if not set(data.task_ids) <= row_ids:
        raise NotFound("所选步骤不属于此任务")
    if data.action == "pause":
        if run.status in TERMINAL or run.status == "paused":
            raise Conflict("已结束的任务不能暂停")
        run.context = {**run.context, "resume_status": run.status}
        run.status = "paused"
    elif data.action == "resume":
        if run.status != "paused":
            raise Conflict("只有暂停的任务可以继续")
        run.status = run.context.get("resume_status", "queued")
        run.error = {}
    elif data.action == "cancel":
        if run.status == "succeeded":
            raise Conflict("已完成任务不能取消，修改请创建新候选")
        from app.modules.harness.transcription_wait import cancel_owned

        cancel_owned(db, run)
        run.status = "canceled"
        for task in rows:
            if task.status not in TERMINAL:
                task.status, task.lease_token, task.lease_expires_at = "canceled", None, None
    elif data.action == "update_limits":
        if run.status not in {"paused", "failed", "needs_attention", "needs_plan_approval"}:
            raise Conflict("请先暂停制作，再调整资源限额")
        run.policy = schemas.Policy.model_validate({**run.policy, **data.limits}).model_dump(
            mode="json"
        )
        for task in rows:
            recoverable_limit = task.error.get("code") == "QUOTA_EXCEEDED" or (
                task.error.get("code") == "ACTION_LIMIT"
                and task.attempts < run.policy["max_actions"]
            )
            if task.status == "failed" and recoverable_limit:
                task.status, task.error = "queued", {}
        run.error = {}
    elif data.action == "approve_plan":
        if run.status != "needs_plan_approval" or not run.plan:
            raise Conflict("没有等待确认的方案")
        if data.budget_points is not None:
            policy = {**run.policy, "budget_points": data.budget_points}
            effects = set(policy["effects"])
            if data.budget_points > 0:
                effects.add("generate")
            else:
                effects.discard("generate")
            policy["effects"] = sorted(effects)
            run.policy = policy
        pending_id = run.context.get("pending_plan_task_id")
        pending = next(
            (
                t
                for t in rows
                if t.status == "waiting_plan" and (pending_id is None or str(t.id) == pending_id)
            ),
            None,
        )
        if pending_id and not pending:
            raise Conflict("待批准的计划已失效，请刷新后重新规划")
        grant = str(data.request_key)
        run.context = {**run.context, "plan_grants": [*run.context.get("plan_grants", []), grant]}
        if pending:
            pending.state = {**pending.state, "plan_grant": grant}
        add_plan(
            db,
            run,
            schemas.Plan.model_validate(
                pending.state.get("pending_plan", run.plan) if pending else run.plan
            ),
            pending,
        )
        if pending:
            pending.state = {k: v for k, v in pending.state.items() if k != "pending_plan"}
        if not pending:
            for task in tasks(db, run):
                if task.revision == run.revision:
                    task.state = {**task.state, "plan_grant": grant}
        run.status = "queued"
        next_plan = next(
            (
                t
                for t in rows
                if t.status == "waiting_plan"
                and t.id != (pending.id if pending else None)
                and t.state.get("pending_plan")
            ),
            None,
        )
        run.context = {
            **run.context,
            "pending_plan_task_id": str(next_plan.id) if next_plan else None,
        }
        if next_plan:
            run.plan = next_plan.state["pending_plan"]
            run.status = "needs_plan_approval"
    elif data.action == "reply":
        targets = [
            t
            for t in rows
            if t.status == "waiting_input" and (not data.task_ids or t.id in data.task_ids)
        ]
        if not targets:
            raise Conflict("没有等待回复的步骤")
        for task in targets:
            task.state = {**task.state, "answer": data.message}
            task.status = "queued"
        run.status = "queued"
    elif data.action == "resume_receipt":
        from app.models.generation import GenerationJob
        from app.modules.harness.plan_contracts import CONTRACT_VERSION, snapshot
        from app.modules.harness.recovery_tools import resume_problem
        from app.modules.tools.capabilities import get_capability

        task = next(t for t in rows if t.id == data.task_ids[0])
        if task.status != "failed" or run.status == "canceled":
            raise Conflict("仅失败步骤可以重新接收已有结果")
        job_id = task.state.get("waiting_job_id")
        job = db.get(GenerationJob, uuid.UUID(job_id)) if job_id else None
        if not job or (
            str(job.input_snapshot.get("harness_origin", {}).get("run_id")) != str(run.id)
        ):
            raise Conflict("缺少此制作任务已提交的生成记录")
        problem = resume_problem(job)
        if problem:
            raise Conflict(problem)
        if task.state.get("receipt_retries", 0) >= run.policy.get("max_retries", 2):
            raise Conflict("重新接收已达到本任务上限，请先核对上游和存储状态")
        capability = get_capability("media.resume_receipt")
        frozen = copy.deepcopy(task.state.get("contract") or task.specification.get("contract", {}))
        frozen["contract_version"] = CONTRACT_VERSION
        frozen.setdefault("tool_contracts", {})["media.resume_receipt"] = snapshot(capability)
        task.state = {
            **task.state,
            "contract": frozen,
            "receipt_retries": task.state.get("receipt_retries", 0) + 1,
            "authorized_receipt_job_id": str(job.id),
            "pending_action": {
                "kind": "tool",
                "tool": "media.resume_receipt",
                "arguments": {"job_id": str(job.id)},
                "message": "继续接收已提交任务的结果，不发起新的生成",
            },
        }
        task.status, task.error = "queued", {}
        run.status, run.error = "queued", {}
    elif data.action == "retry":
        targets = [
            t for t in rows if t.status == "failed" and (not data.task_ids or t.id in data.task_ids)
        ]
        if not targets:
            raise Conflict("没有可以重试的失败步骤；提交结果不确定时请先核对上游状态")
        for task in targets:
            if task.error.get("retryable") is not True:
                raise Conflict("此步骤不能直接重试，需要修正输入或核对执行状态")
            if task.state.get("retries", 0) >= run.policy["max_retries"]:
                raise Conflict("此步骤已达到重试上限")
        for task in targets:
            task.state = {**task.state, "retries": task.state.get("retries", 0) + 1}
            task.status, task.error = "queued", {}
        run.status = "queued"
    elif data.action in {"approve_tool", "reject_tool"}:
        task = next(t for t in rows if t.id == data.task_ids[0])
        if task.status != "waiting_review" or task.state.get("review_id") != str(data.review_id):
            raise Conflict("审阅记录已经变化，请刷新后再决定")
        attempt = db.get(ProductionAttempt, data.review_id)
        if not attempt or attempt.task_id != task.id or not attempt.result.get("review"):
            raise NotFound("审阅记录不存在")
        result = {"decision": data.action, "message": data.message}
        if data.action == "approve_tool":
            proposal = attempt.result["review"]
            # Paid submissions use media.generate's persistent authorization ledger.
            # Do not let a legacy proposal create a second ungoverned submission path.
            if proposal["tool"] != "object.propose_edit":
                raise Conflict("此提案需在对应工程审阅中处理，生成请使用已授权制作计划")
            from app.modules.tools.contracts import ToolContext
            from app.modules.tools.registry import apply_review

            result["applied"] = apply_review(
                db,
                ctx,
                ToolContext(
                    scope=task.specification.get("scope") or [],
                    work_id=str(run.work_id) if run.work_id else None,
                    skill_snapshot=run.context.get("skills", []),
                ),
                proposal,
            )
            from fastapi.encoders import jsonable_encoder

            result = jsonable_encoder(result)
        task.state = {**task.state, "review_decision": result}
        task.status = (
            "queued"
            if task.kind == "agent"
            else "succeeded"
            if data.action == "approve_tool"
            else "failed"
        )
        task.result = result
        run.status = "queued"
    elif data.action == "steer":
        if run.status == "canceled":
            raise Conflict("已取消任务不能继续，请创建新任务")
        # Replace each affected boundary independently. A delegated task has one
        # parent and one grant; joining distinct branches into a root coordinator
        # would silently widen its scope and corrupt both parents' child lists.
        active = [t for t in rows if t.status != "superseded"]
        active_ids = {t.id for t in active}
        if data.task_ids and not set(data.task_ids) <= active_ids:
            raise Conflict("所选步骤已被替代，请选择当前步骤再指导")
        affected = affected_tasks(active, data.task_ids or list(active_ids))
        by_id = {t.id: t for t in active}
        while True:
            # An ancestor's aggregate becomes stale when any descendant changes.
            # Re-run that aggregation while retaining siblings, and invalidate
            # consumers of its output exactly as consumers of a direct edit.
            ancestors = set()
            for task_id in affected:
                parent_id = by_id[task_id].parent_id
                while parent_id in by_id:
                    ancestors.add(parent_id)
                    parent_id = by_id[parent_id].parent_id
            consumers = {
                t.id for t in active if any(uuid.UUID(d) in ancestors for d in t.dependencies)
            }
            expanded = affected_tasks(active, affected | consumers)
            if expanded == affected:
                break
            affected = expanded
        aggregators = ancestors - affected
        from app.modules.harness.transcription_wait import cancel_owned

        cancel_owned(db, run, affected | aggregators)
        boundaries = [t for t in active if t.id in affected and t.parent_id not in affected]
        if not boundaries:
            raise Conflict("没有可以调整的当前步骤")
        if len(rows) + len(boundaries) > run.policy["max_tasks"]:
            raise Conflict("制作任务数量已达上限，请缩小计划")
        owners = {}
        for task_id in affected:
            owner = by_id[task_id]
            while owner.parent_id in affected:
                owner = by_id[owner.parent_id]
            owners[task_id] = owner.id
        parent_children = {
            t.parent_id: copy.deepcopy(by_id[t.parent_id].state.get("children", []))
            for t in boundaries
            if t.parent_id
        }
        for task in rows:
            if task.id in affected and task.status != "superseded":
                task.status, task.lease_token, task.lease_expires_at = "superseded", None, None
        run.revision += 1
        replacements = {}
        for index, original in enumerate(boundaries):
            parent = by_id.get(original.parent_id)
            prior = original.specification
            key = f"revision_{run.revision}_{index}"
            # Full source goals and user text remain in immutable tasks/events;
            # the bounded replan instruction is only a navigation summary.
            goal = (
                f"原制作目标：{run.goal[:500]}\n原步骤目标：{original.goal[:1000]}\n"
                f"用户本次指导：{data.message[:6000]}\n"
                "只重新规划此替代边界，保留未受影响的成果；完整指导见用户事件。"
            )
            tool_names = list(
                dict.fromkeys(
                    [*prior.get("tools", []), *([prior["tool"]] if prior.get("tool") else [])]
                )
            )
            add_plan(
                db,
                run,
                schemas.Plan(
                    summary=data.message[:4000],
                    tasks=[
                        schemas.TaskSpec(
                            key=key,
                            goal=goal,
                            role="coordinator",
                            tools=tool_names,
                            scope=prior.get("scope"),
                            allowed_tools=prior.get("allowed_tools"),
                            references=prior.get("references", []),
                            expected_output=prior.get("expected_output", "reply"),
                            output_requirements=prior.get("output_requirements", {}),
                            quality_requirements=prior.get("quality_requirements", {}),
                        )
                    ],
                ),
                parent,
            )
            stored_key = f"{parent.id.hex[:10]}.{key}" if parent else key
            replacement = next(t for t in tasks(db, run) if t.key == stored_key)
            replacement.key = key
            replaced = sorted((v for v in affected if owners[v] == original.id), key=str)
            state = {
                **replacement.state,
                "replaces": [str(v) for v in replaced],
                "loaded_tools": list(
                    dict.fromkeys([*tool_names, *original.state.get("loaded_tools", [])])
                ),
            }
            # Steering changes intent, never the previously read tool contract.
            frozen = original.state.get("contract") or prior.get("contract")
            if frozen:
                state["contract"] = copy.deepcopy(frozen)
            reads = copy.deepcopy(state.get("read_preconditions", []))
            for old_id in replaced:
                for read in by_id[old_id].state.get("read_preconditions", []):
                    if read not in reads:
                        reads.append(read)
            if reads:
                state["read_preconditions"] = reads
            if original.state.get("plan_grant"):
                state["plan_grant"] = original.state["plan_grant"]
            replacement.state = state
            replacements[original.id] = replacement
        for original in boundaries:
            replacement = replacements[original.id]
            dependencies = []
            for old_id, owner_id in owners.items():
                if owner_id != original.id:
                    continue
                for dependency in by_id[old_id].dependencies:
                    target = uuid.UUID(dependency)
                    if target in owners:
                        target = replacements[owners[target]].id
                    if target != replacement.id and str(target) not in dependencies:
                        dependencies.append(str(target))
            replacement.dependencies = dependencies
        for parent_id, children in parent_children.items():
            parent = by_id[parent_id]
            parent.state = {
                **parent.state,
                "children": [
                    str(replacements[uuid.UUID(c)].id) if uuid.UUID(c) in replacements else c
                    for c in children
                ],
            }
            parent.status = "waiting_children"
        for parent_id in aggregators:
            parent = by_id[parent_id]
            # A previously decided action/validation cannot survive a changed
            # child result. Durable old attempts and artifacts remain available.
            parent.state = {
                key: value
                for key, value in parent.state.items()
                if key
                in {
                    "children",
                    "plan_grant",
                    "read_preconditions",
                    "contract",
                    "loaded_tools",
                    "replaces",
                }
            }
            parent.state = {**parent.state, "evidence_invalid_before": now().isoformat()}
            parent.status, parent.lease_token, parent.lease_expires_at = (
                "waiting_children",
                None,
                None,
            )
            parent.error = {}
        run.context = {
            **run.context,
            "facts": [
                *run.context.get("facts", []),
                {
                    "id": str(data.request_key),
                    "kind": "user_decision",
                    "source_id": str(data.request_key),
                    "sequence": run.event_sequence + 1,
                    "text": data.message,
                    "scope": "project" if not data.task_ids else "selected_tasks",
                    "evidence": {
                        "task_ids": [str(v) for v in affected],
                        "propagation_task_ids": [str(v) for v in aggregators],
                    },
                    "required": True,
                },
            ],
        }
        run.status, run.plan, run.result = "queued", {}, {}
    if data.action != "steer":
        run.revision += 1
    if was_paused and data.action not in {"resume", "cancel", "pause"}:
        run.context = {**run.context, "resume_status": run.status}
        run.status = "paused"
    event(
        db,
        run,
        f"user.{data.action}",
        {"request": data.model_dump(mode="json")},
        actor_id=ctx.user.id,
        request_key=data.request_key,
    )
    db.flush()
    reconcile(db, run)
    return out(db, run)


def reconcile(db, run):
    rows = tasks(db, run)
    by_id = {str(t.id): t for t in rows}
    for task in rows:
        if task.status in {"waiting_children", "blocked"} and task.state.get("children"):
            children = [by_id[k] for k in task.state.get("children", [])]
            if children and all(c.status == "succeeded" for c in children):
                task.state = {
                    **task.state,
                    "child_results": [{"id": str(c.id), "result": c.result} for c in children],
                }
                task.status = "queued"
            elif any(c.status in {"failed", "superseded", "canceled"} for c in children):
                task.status = "blocked"
        elif task.status == "queued" and any(
            by_id[d].status in {"failed", "superseded", "canceled"} for d in task.dependencies
        ):
            task.status = "blocked"
        elif (
            task.status == "blocked"
            and task.dependencies
            and all(by_id[d].status == "succeeded" for d in task.dependencies)
        ):
            task.status = "queued"
    if run.status in {"paused", "canceled", "needs_plan_approval"}:
        return
    if run.context.get("guidance_pending"):
        impact = next(
            (t for t in rows if t.state.get("guidance_id") == run.context["guidance_pending"]), None
        )
        run.status = (
            "running" if impact and impact.status in {"queued", "running"} else "needs_attention"
        )
        return
    active = [t for t in rows if t.status != "superseded"]
    if active and all(t.status == "succeeded" for t in active):
        run.status = "succeeded"
        run.result = {
            **run.result,
            "tasks": [{"id": str(t.id), "result": t.result} for t in active if not t.parent_id],
        }
    elif any(
        (
            t.status in {"running", "waiting_job", "validating_job", "waiting_transcription"}
            or (
                t.status == "queued" and all(by_id[d].status == "succeeded" for d in t.dependencies)
            )
        )
        for t in active
    ):
        run.status = "running"
    elif any(
        t.status in {"waiting_input", "waiting_review", "waiting_candidate", "uncertain"}
        for t in active
    ):
        run.status = "needs_attention"
    elif any(t.status in {"failed", "blocked"} for t in active):
        run.status = "failed"


def claim(db, run_id, *, lease_seconds=180):
    run = db.scalar(select(ProductionRun).where(ProductionRun.id == run_id).with_for_update())
    if not run or run.status in TERMINAL:
        return None
    if (
        run.context.get("format_version", 1) != 1
        or run.context.get("kernel_protocol_version", 1) != 1
    ):
        run.status = "paused"
        run.error = {
            "code": "FORMAT_VERSION_UNSUPPORTED",
            "message": "任务格式或内核协议需要迁移，已有成果已保留",
        }
        return None
    execution_context(db, run)
    from app.modules.harness.job_wait import poll_waiting, recover_uncertain

    recover_uncertain(db, run)
    poll_waiting(db, run)
    from app.modules.harness import transcription_wait

    transcription_wait.recover_uncertain(db, run)
    transcription_wait.poll_waiting(db, run)
    from app.modules.harness.candidate_wait import reconcile as reconcile_candidates

    reconcile_candidates(db, run)
    reconcile(db, run)
    from app.modules.harness import guidance

    impact_pending = guidance.pending(run)
    was_paused = run.status == "paused"
    if run.status not in {"queued", "running"} and not (impact_pending and was_paused):
        return None
    rows = tasks(db, run)
    if sum(t.attempts for t in rows) >= run.policy.get("max_total_actions", 240):
        run.context = {**run.context, "resume_status": "queued"}
        run.status, run.error = (
            "paused",
            {"code": "TOTAL_ACTION_LIMIT", "message": "制作任务已达到累计执行上限，成果已保留"},
        )
        return None
    from app.modules.harness import resources

    if resources.active_seconds(db, run) >= run.policy.get("max_active_seconds", 7200):
        run.context = {**run.context, "resume_status": "queued"}
        run.status, run.error = (
            "paused",
            {"code": "ACTIVE_TIME_LIMIT", "message": "制作已达到累计执行时间上限，成果已保留"},
        )
        return None
    for task in rows:
        if task.status == "running" and task.lease_expires_at and task.lease_expires_at < now():
            # An expired lease is not proof that an external call never happened.
            task.status, task.lease_token = "uncertain", None
            task.error = {
                "code": "LEASE_EXPIRED",
                "message": "执行中断，先核对调用结果后再继续",
                "retryable": False,
            }
            event(db, run, "task.uncertain", {"task_id": str(task.id)})
    reconcile(db, run)
    if (
        not impact_pending
        and sum(t.status == "running" for t in rows) >= run.policy["max_parallel"]
    ):
        return None
    completed = {str(t.id) for t in rows if t.status == "succeeded"}
    task = next(
        (
            t
            for t in rows
            if t.status in {"queued", "validating_job"}
            and set(t.dependencies) <= completed
            and (not impact_pending or guidance.is_impact(t))
        ),
        None,
    )
    if not task:
        return None
    if not resources.capacity(db, run):
        return None
    if task.attempts >= run.policy["max_actions"]:
        task.status, task.error = (
            "failed",
            {"code": "ACTION_LIMIT", "message": "步骤已达到执行上限", "retryable": False},
        )
        reconcile(db, run)
        return None
    token = uuid.uuid4()
    task.status, task.lease_token, task.lease_expires_at = (
        "running",
        token,
        now() + timedelta(seconds=lease_seconds),
    )
    task.attempts += 1
    attempt = ProductionAttempt(
        task_id=task.id,
        number=task.attempts,
        lease_token=token,
        operation={"phase": "claimed"},
        result={},
        error={},
    )
    db.add(attempt)
    run.status = "paused" if was_paused else "running"
    event(db, run, "task.started", {"task_id": str(task.id), "attempt": task.attempts})
    db.flush()
    return {
        "run_id": str(run.id),
        "task_id": str(task.id),
        "attempt_id": str(attempt.id),
        "lease_token": str(token),
    }


def renew_lease(db, ticket, *, lease_seconds=180):
    run = db.scalar(
        select(ProductionRun)
        .where(ProductionRun.id == uuid.UUID(ticket["run_id"]))
        .with_for_update()
    )
    task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]), populate_existing=True)
    if (
        not run
        or not task
        or task.run_id != run.id
        or task.status != "running"
        or task.lease_token != uuid.UUID(ticket["lease_token"])
        or not task.lease_expires_at
        or task.lease_expires_at < now()
    ):
        return False
    task.lease_expires_at = now() + timedelta(seconds=lease_seconds)
    return True


def finish_attempt(db, ticket, *, result=None, error=None, status="queued", state=None):
    run = db.scalar(
        select(ProductionRun)
        .where(ProductionRun.id == uuid.UUID(ticket["run_id"]))
        .with_for_update()
    )
    task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
    attempt = db.get(ProductionAttempt, uuid.UUID(ticket["attempt_id"]))
    if (
        not run
        or not task
        or not attempt
        or task.run_id != run.id
        or attempt.task_id != task.id
        or attempt.lease_token != uuid.UUID(ticket["lease_token"])
    ):
        raise NotFound("执行凭据不存在")
    if attempt.status in {"succeeded", "failed", "late_result"}:
        return False
    from app.modules.harness.resources import record_elapsed

    record_elapsed(attempt)
    if task.status == "running" and task.lease_expires_at and task.lease_expires_at < now():
        task.status, task.lease_token = "uncertain", None
        task.error = {
            "code": "LEASE_EXPIRED",
            "message": "结果在执行租约到期后返回，需要核对",
            "retryable": False,
        }
    attempt.result, attempt.error = result or {}, error or {}
    if task.lease_token != attempt.lease_token or task.status != "running":
        attempt.status = "late_result"
        event(
            db, run, "attempt.late_result", {"task_id": str(task.id), "attempt_id": str(attempt.id)}
        )
        reconcile(db, run)
        return False
    attempt.status = "failed" if error else "succeeded"
    task.status, task.lease_token, task.lease_expires_at = status, None, None
    task.result, task.error = result or {}, error or {}
    if state is not None:
        task.state = copy.deepcopy(state)
    event(
        db,
        run,
        "task.updated",
        {"task_id": str(task.id), "status": status, "attempt_id": str(attempt.id)},
    )
    db.flush()
    reconcile(db, run)
    return True
