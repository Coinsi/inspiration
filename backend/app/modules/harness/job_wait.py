"""Reconcile durable media jobs without submitting or charging a second time.

Callers hold the production run row lock. These functions never dispatch work or
commit transactions. Paused runs may receive finished receipts but stay paused;
canceled/superseded tasks never publish late output into the current plan.
"""

import copy
import hashlib
import json
import uuid

from sqlalchemy import select

from app.core.errors import AppError
from app.models.generation import GenerationJob
from app.models.harness import ProductionAttempt, ProductionTask
from app.modules.harness import service
from app.modules.harness.media_tools import observe_job


def _receipt(data):
    return {
        "tool": "media.job_observe",
        "version": 1,
        "contract_version": 1,
        "effect": "read",
        "status": "succeeded",
        "data": data,
        "adopted": False,
    }


def _record(db, run, task, kind, payload):
    item = {"task_id": str(task.id), **payload}
    service.event(db, run, kind, item)
    return {"kind": kind, **item}


def _store_receipt(task, receipt):
    state = copy.deepcopy(task.state)
    fingerprint = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    if state.get("waiting_job_fingerprint") == fingerprint:
        return False
    state["waiting_job_fingerprint"] = fingerprint
    state["job_receipt"] = receipt
    task.state = state
    return True


def poll_waiting(db, run):
    """Refresh waiting_job tasks and return newly recorded transition events."""
    if run.status in {"canceled", "superseded"}:
        return []
    ctx = service.execution_context(db, run)
    changed = []
    rows = list(
        db.scalars(
            select(ProductionTask)
            .where(
                ProductionTask.run_id == run.id,
                ProductionTask.status == "waiting_job",
            )
            .order_by(ProductionTask.created_at, ProductionTask.id)
        )
    )
    for task in rows:
        try:
            job_id = uuid.UUID(str(task.state.get("waiting_job_id")))
        except (ValueError, TypeError):
            task.status = "uncertain"
            task.error = {
                "code": "JOB_REFERENCE_MISSING",
                "message": "缺少已提交任务标识，请核对执行记录",
                "retryable": False,
            }
            changed.append(_record(db, run, task, "task.job_unresolved", task.error))
            continue
        job = db.get(GenerationJob, job_id, populate_existing=True)
        if not job or job.project_id != run.project_id or job.created_by != run.created_by:
            task.status = "uncertain"
            task.error = {
                "code": "JOB_REFERENCE_INVALID",
                "message": "已提交任务不可访问，请核对记录",
                "retryable": False,
            }
            changed.append(_record(db, run, task, "task.job_unresolved", task.error))
            continue
        try:
            data = observe_job(db, ctx, job, check_storage=False)
        except AppError as exc:
            task.status = "uncertain"
            task.error = {"code": exc.code, "message": exc.message, "retryable": False}
            changed.append(_record(db, run, task, "task.job_unresolved", task.error))
            continue
        receipt = _receipt(data)
        updated = _store_receipt(task, receipt)
        if job.status in {"failed", "canceled"}:
            from app.modules.harness.recovery_tools import resume_problem

            uncertain = (job.routing_state or {}).get("harness_delivery", {}).get(
                "recovery"
            ) == "uncertain" or (job.routing_state or {}).get("phase") in {
                "dispatching",
                "submission_unknown",
            }
            task.status = "uncertain" if uncertain else "failed"
            task.error = {
                "code": "GENERATION_SUBMISSION_UNCERTAIN"
                if uncertain
                else "GENERATION_JOB_FAILED"
                if job.status == "failed"
                else "GENERATION_JOB_CANCELED",
                "message": job.error or "生成任务已取消，已有产物保留",
                "job_id": str(job.id),
                "retryable": False,
                "recovery": "resume_receipt"
                if not uncertain and resume_problem(job) is None
                else "inspect_existing_job_before_authorizing_new_generation",
            }
            task.result = receipt
            changed.append(_record(db, run, task, "task.job_failed", task.error))
        elif job.status == "succeeded" and data["records_ready"]:
            state = copy.deepcopy(task.state)
            state["validation_job_id"] = str(job.id)
            state["steps"] = [
                *state.get("steps", []),
                {
                    "fingerprint": state["waiting_job_fingerprint"],
                    "receipt": receipt,
                },
            ][-20:]
            task.state, task.error, task.result = state, {}, receipt
            task.status = "validating_job"
            changed.append(
                _record(
                    db,
                    run,
                    task,
                    "task.job_completed",
                    {
                        "job_id": str(job.id),
                        "status": task.status,
                        "records_ready": True,
                        "materialized": False,
                    },
                )
            )
        elif job.status == "succeeded":
            task.error = {
                "code": "RESULT_NOT_MATERIALIZED",
                "message": "上游已完成，但成果尚不可读取",
                "job_id": str(job.id),
                "retryable": False,
                "recovery": "repair_or_reconcile_existing_output",
            }
            if updated:
                changed.append(_record(db, run, task, "task.output_unavailable", task.error))
        elif updated:
            changed.append(
                _record(
                    db,
                    run,
                    task,
                    "task.job_progress",
                    {
                        "job_id": str(job.id),
                        "status": job.status,
                    },
                )
            )
    db.flush()
    return changed


def _recover_generation(db, run, task, attempt):
    if attempt.operation.get("tool") == "media.resume_receipt":
        from app.modules.harness.recovery_tools import MARKER

        try:
            job = db.get(GenerationJob, uuid.UUID(attempt.operation["arguments"]["job_id"]))
        except (KeyError, TypeError, ValueError):
            return []
        if not job or job.project_id != run.project_id or job.created_by != run.created_by:
            return []
        saved = (job.routing_state or {}).get(MARKER, {})
        frozen = saved.get("frozen", {})
        if (
            frozen.get("run_id") != str(run.id)
            or frozen.get("task_id") != str(task.id)
            or str(attempt.id) not in saved.get("request_keys", [])
        ):
            return []
        return [job]
    origin = GenerationJob.input_snapshot["harness_origin"]
    return list(
        db.scalars(
            select(GenerationJob)
            .where(
                GenerationJob.project_id == run.project_id,
                GenerationJob.created_by == run.created_by,
                origin["run_id"].astext == str(run.id),
                origin["task_id"].astext == str(task.id),
                origin["request_key"].astext == str(attempt.id),
            )
            .limit(2)
        )
    )


def _recover_preview(db, ctx, run, attempt):
    from app.modules.engineering import review
    from app.modules.engineering import service as engineering

    args = attempt.operation.get("arguments", {})
    try:
        work_id, change_id = uuid.UUID(args["work_id"]), uuid.UUID(args["change_id"])
        if work_id != run.work_id:
            return []
        engineering.work(db, ctx, work_id)
        change = review.get_change(db, work_id, change_id)
        if change.fingerprint != args.get("fingerprint"):
            return []
    except (KeyError, ValueError, TypeError, AppError):
        return []
    output_id = args.get("output_id")
    if output_id is None and not change.timeline_update:
        roots = change.snapshot.get("native_roots", {})
        if len(roots) != 1:
            return []
        output_id = next(iter(roots))
    # Match the exact business idempotency key used by review.preview, including
    # a previously failed job's explicit retry. Never pick a different output.
    job = review.find_preview(db, ctx, change, output_id)
    return [job] if job and job.created_by == run.created_by else []


def recover_uncertain(db, run):
    """Reattach only an unambiguous persisted job matching the original operation.

    A missing match is not proof that no external submission occurred. Leave it
    uncertain rather than creating a fresh attempt or replacing a request key.
    """
    if run.status in {"canceled", "superseded"}:
        return []
    ctx = service.execution_context(db, run)
    changed = []
    rows = list(
        db.scalars(
            select(ProductionTask)
            .where(
                ProductionTask.run_id == run.id,
                ProductionTask.status == "uncertain",
            )
            .order_by(ProductionTask.created_at, ProductionTask.id)
        )
    )
    for task in rows:
        if task.error.get("code") == "GENERATION_SUBMISSION_UNCERTAIN":
            # This is a confirmed ambiguous provider submission, not a lost
            # tool receipt. Reattaching it each sweep would hide the attention
            # state without making progress and must never submit it again.
            continue
        attempt = db.scalar(
            select(ProductionAttempt)
            .where(ProductionAttempt.task_id == task.id)
            .order_by(ProductionAttempt.number.desc())
            .limit(1)
        )
        if not attempt:
            continue
        phase = attempt.operation.get("phase")
        if phase == "decision" and task.state.get("decision_recoveries", 0) < run.policy.get(
            "max_retries", 2
        ):
            # A lost decision has no tool side effects. Its model reservation is
            # retained; a new bounded decision uses the current application state.
            task.state = {
                **task.state,
                "decision_recoveries": task.state.get("decision_recoveries", 0) + 1,
            }
            task.status, task.lease_token, task.lease_expires_at, task.error = (
                "queued",
                None,
                None,
                {},
            )
            attempt.status = "reconciled"
            changed.append(
                _record(db, run, task, "task.decision_recovered", {"attempt_id": str(attempt.id)})
            )
            continue
        if phase in {"claimed", "validate"}:
            # Neither phase sends a model/provider request. Revalidate using the
            # same persisted output; never submit a new generation to recover it.
            task.status = "validating_job" if phase == "validate" else "queued"
            task.lease_token, task.lease_expires_at, task.error = None, None, {}
            attempt.status = "reconciled"
            changed.append(_record(db, run, task, "task.safe_recovered", {"phase": phase}))
            continue
        if phase != "tool":
            continue
        name = attempt.operation.get("tool")
        if name in {"engineering.compose", "engineering.propose", "engineering.selective_edit"}:
            from app.models.engineering import EngineeringChange

            key = (
                attempt.operation.get("arguments", {}).get("request_key")
                if name == "engineering.propose"
                else str(attempt.id)
            )
            try:
                change = db.scalar(
                    select(EngineeringChange).where(
                        EngineeringChange.work_id == run.work_id,
                        EngineeringChange.request_key == uuid.UUID(str(key)),
                    )
                )
            except (ValueError, TypeError):
                change = None
            if change:
                receipt = {
                    "tool": name,
                    "status": "succeeded",
                    "adopted": False,
                    "data": {
                        "id": str(change.id),
                        "change_id": str(change.id),
                        "work_id": str(change.work_id),
                        "fingerprint": change.fingerprint,
                        "base_revision": change.base_revision,
                        "status": change.status,
                        "recovered": True,
                        "note": "候选写入已核实；需要预览时读取候选选择固定输出，不重新制作。",
                    },
                }
                task.state = {
                    **task.state,
                    "steps": [*task.state.get("steps", []), {"receipt": receipt}][-20:],
                }
                task.status = "succeeded" if task.kind == "tool" else "queued"
                task.result, task.error, task.lease_token, task.lease_expires_at = (
                    receipt,
                    {},
                    None,
                    None,
                )
                attempt.status, attempt.result = "reconciled", receipt
                changed.append(
                    _record(
                        db, run, task, "task.candidate_recovered", {"change_id": str(change.id)}
                    )
                )
            # A missing synchronous transaction cannot have made an external
            # call, but retain uncertainty until its original transaction exits.
            continue
        if name == "work.prepare" and run.work_id:
            task.status, task.error = "queued", {}
            task.lease_token, task.lease_expires_at = None, None
            attempt.status = "reconciled"
            changed.append(_record(db, run, task, "task.safe_recovered", {"tool": name}))
            continue
        if name in {"media.generate", "media.process", "media.resume_receipt"}:
            matches = _recover_generation(db, run, task, attempt)
        elif name == "engineering.preview":
            matches = _recover_preview(db, ctx, run, attempt)
        else:
            from app.modules.tools.capabilities import get_capability

            try:
                cap = get_capability(name)
            except AppError:
                continue
            if cap.policy and cap.policy.idempotency in {"read_only", "proposal_only"}:
                task.status, task.error = "queued", {}
                task.lease_token, task.lease_expires_at = None, None
                attempt.status = "reconciled"
                changed.append(_record(db, run, task, "task.safe_recovered", {"tool": name}))
            continue
        if len(matches) != 1:
            continue
        job = matches[0]
        if name == "engineering.preview":
            # The preview can commit before runtime.apply binds its reservation
            # to the returned job. Restore that exact binding after a lost
            # receipt, or settle_media would retain the full estimate forever.
            resource_key = "preview:" + str(attempt.id)
            entries = copy.deepcopy(run.context.get("resource_reservations", {}))
            entry = entries.get(resource_key)
            if entry is not None:
                args = attempt.operation.get("arguments", {})
                selection = {
                    "change_id": args.get("change_id"),
                    "output_id": args.get("output_id"),
                }
                if run.context.get("preview_reservations", {}).get(
                    str(attempt.id)
                ) != selection or entry.get("job_id") not in {None, str(job.id)}:
                    continue  # Never settle another candidate's reservation.
                entries[resource_key] = {**entry, "job_id": str(job.id)}
                run.context = {**run.context, "resource_reservations": entries}
        task.status, task.lease_token, task.lease_expires_at, task.error = (
            "waiting_job",
            None,
            None,
            {},
        )
        task.state = {
            **task.state,
            "waiting_job_id": str(job.id),
            "recovered_attempt_id": str(attempt.id),
        }
        attempt.status = "reconciled"
        attempt.result = {**attempt.result, "recovered_job_id": str(job.id)}
        changed.append(
            _record(
                db,
                run,
                task,
                "task.job_recovered",
                {
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id),
                    "resubmitted": False,
                },
            )
        )
    db.flush()
    return changed
