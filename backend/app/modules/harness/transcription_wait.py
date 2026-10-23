"""Reconcile the separate transcription queue; never resubmit recognition.

Call under the production run row lock. These functions do not call a model,
commit, publish annotations, or dispatch successors. A paused run keeps its pause.
"""

import copy
import hashlib
import json
import uuid

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.core.errors import AppError, Conflict
from app.models.harness import ProductionAttempt, ProductionTask
from app.modules.harness import service
from app.modules.harness.media_context import describe_media
from app.modules.harness.transcription_tools import observe
from app.modules.transcription import schemas
from app.modules.transcription import service as transcription


def _receipt(data):
    return jsonable_encoder(
        {
            "tool": "media.transcription_status",
            "version": 1,
            "contract_version": 1,
            "effect": "read",
            "status": "succeeded",
            "data": data,
            "adopted": False,
        }
    )


def _entry(run, task, ident):
    entries = [
        entry
        for entry in run.context.get("transcription_reservations", {}).values()
        if entry.get("task_id") == str(task.id) and entry.get("transcription_id") == str(ident)
    ]
    if len(entries) != 1:
        raise Conflict("转写结果缺少唯一的本任务提交记录")
    return entries[0]


def _checked(db, ctx, run, task, ident):
    entry = _entry(run, task, ident)
    row = transcription.get(db, ctx, ident)
    if row.created_by != run.created_by or str(row.version_id) != entry["source"]["id"]:
        raise Conflict("转写任务不属于当前制作请求")
    describe_media(db, ctx, entry["source"])
    if row.status == "ready":
        data = schemas.Save(revision=row.revision, cues=row.cues)
        if row.completed != row.total or any(c.end_ms > entry["duration_ms"] for c in data.cues):
            raise Conflict("转写检查点或字幕范围不完整，请校对现有结果")
    return row, observe(db, ctx, ident)


def _record(db, run, task, kind, payload):
    item = {"task_id": str(task.id), **payload}
    service.event(db, run, kind, item)
    return {"kind": kind, **item}


def poll_waiting(db, run):
    if run.status in {"canceled", "superseded"}:
        return []
    ctx = service.execution_context(db, run)
    changed = []
    for task in db.scalars(
        select(ProductionTask).where(
            ProductionTask.run_id == run.id, ProductionTask.status == "waiting_transcription"
        )
    ):
        try:
            ident = uuid.UUID(str(task.state.get("waiting_transcription_id")))
            row, data = _checked(db, ctx, run, task, ident)
        except (AppError, ValueError, TypeError) as exc:
            task.status = "uncertain"
            task.error = {
                "code": "TRANSCRIPTION_REFERENCE_INVALID",
                "message": str(exc)[:400],
                "retryable": False,
            }
            changed.append(_record(db, run, task, "task.transcription_unresolved", task.error))
            continue
        receipt = _receipt(data)
        fingerprint = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
        state = copy.deepcopy(task.state)
        updated = state.get("transcription_fingerprint") != fingerprint
        state.update(transcription_fingerprint=fingerprint, transcription_receipt=receipt)
        task.state = state
        if row.status in {"failed", "canceled"}:
            task.status = "failed"
            task.result = receipt
            task.error = {
                "code": "TRANSCRIPTION_FAILED",
                "message": row.error or "转写已取消，检查点保留",
                "transcription_id": str(row.id),
                "retryable": False,
                "recovery": "inspect_existing_transcription_before_explicit_resume",
            }
            changed.append(_record(db, run, task, "task.transcription_failed", task.error))
        elif row.status == "ready":
            if task.kind == "tool":
                from app.modules.harness.verification import verify_output

                try:
                    verify_output(
                        db,
                        ctx,
                        task.specification.get("expected_output", "observation"),
                        [receipt],
                        work_id=run.work_id,
                    )
                except AppError as exc:
                    task.status = "failed"
                    task.result = receipt
                    task.error = {
                        "code": "TRANSCRIPTION_OUTPUT_MISMATCH",
                        "message": exc.message,
                        "retryable": False,
                    }
                    changed.append(_record(db, run, task, "task.transcription_failed", task.error))
                    continue
            state["steps"] = [
                *state.get("steps", []),
                {"fingerprint": fingerprint, "receipt": receipt},
            ][-20:]
            task.state = state
            task.result, task.error = receipt, {}
            task.status = "succeeded" if task.kind == "tool" else "queued"
            changed.append(
                _record(
                    db,
                    run,
                    task,
                    "task.transcription_completed",
                    {
                        "transcription_id": str(row.id),
                        "cue_count": len(row.cues),
                        "requires_review": True,
                        "word_alignment": False,
                    },
                )
            )
        elif updated:
            changed.append(
                _record(
                    db,
                    run,
                    task,
                    "task.transcription_progress",
                    {
                        "transcription_id": str(row.id),
                        "completed": row.completed,
                        "total": row.total,
                    },
                )
            )
    db.flush()
    return changed


def recover_uncertain(db, run):
    if run.status in {"canceled", "superseded"}:
        return []
    ctx = service.execution_context(db, run)
    changed = []
    for task in db.scalars(
        select(ProductionTask).where(
            ProductionTask.run_id == run.id, ProductionTask.status == "uncertain"
        )
    ):
        attempt = db.scalar(
            select(ProductionAttempt)
            .where(ProductionAttempt.task_id == task.id)
            .order_by(ProductionAttempt.number.desc())
            .limit(1)
        )
        if (
            not attempt
            or attempt.operation.get("phase") != "tool"
            or attempt.operation.get("tool") != "media.transcribe"
        ):
            continue
        entry = run.context.get("transcription_reservations", {}).get(str(attempt.id))
        if not entry or entry.get("task_id") != str(task.id):
            continue
        try:
            ident = uuid.UUID(entry["transcription_id"])
            _checked(db, ctx, run, task, ident)
        except (AppError, ValueError, TypeError, KeyError):
            continue
        task.status, task.error = "waiting_transcription", {}
        task.lease_token, task.lease_expires_at = None, None
        task.state = {
            **task.state,
            "waiting_transcription_id": str(ident),
            "recovered_attempt_id": str(attempt.id),
        }
        attempt.status = "reconciled"
        attempt.result = {**attempt.result, "recovered_transcription_id": str(ident)}
        changed.append(
            _record(db, run, task, "task.transcription_recovered", {"transcription_id": str(ident)})
        )
    db.flush()
    return changed


def cancel_owned(db, run, task_ids=None):
    """Cancel only ASR intents owned by selected production steps; retain drafts."""
    ctx = service.execution_context(db, run)
    selected = None if task_ids is None else {str(x) for x in task_ids}
    canceled = []
    for entry in run.context.get("transcription_reservations", {}).values():
        if selected is not None and entry.get("task_id") not in selected:
            continue
        ident = uuid.UUID(entry["transcription_id"])
        try:
            row = transcription.get(db, ctx, ident, lock=True)
        except AppError:
            continue
        if row.created_by != run.created_by or str(row.version_id) != entry["source"]["id"]:
            continue
        if row.status in {"queued", "processing"} and not row.cancel_requested:
            row.cancel_requested = True
            if row.status == "queued":
                row.status = "canceled"
            canceled.append(str(ident))
    db.flush()
    return canceled
