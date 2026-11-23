"""Recover a quota-blocked generation step only before any durable submission."""

import copy

from sqlalchemy import select

from app.models.creation import Creation
from app.models.generation import GenerationJob
from app.models.harness import ProductionAttempt


def generation_quota_attempt(db, task):
    if task.error.get("code") != "QUOTA_EXCEEDED":
        return None
    attempt = db.scalar(
        select(ProductionAttempt)
        .where(ProductionAttempt.task_id == task.id)
        .order_by(ProductionAttempt.number.desc())
        .limit(1)
    )
    if attempt and attempt.operation.get("tool") == "media.generate":
        return attempt
    return None


def blocked_generation_action(db, run, task):
    # Legacy failures have no structured credit detail. Their saved attempt and
    # absence of a submitted job establish the same boundary as newer failures.
    if (
        task.status != "failed"
        or task.error.get("code") != "QUOTA_EXCEEDED"
        or run.status in {"canceled", "succeeded", "superseded"}
        or task.state.get("waiting_job_id")
        or task.state.get("authorized_receipt_job_id")
        or task.error.get("recovery") == "resume_receipt"
    ):
        return None
    attempt = generation_quota_attempt(db, task)
    if (
        not attempt
        or attempt.status != "failed"
        or not isinstance(attempt.operation.get("arguments"), dict)
    ):
        return None
    existing = db.scalar(
        select(GenerationJob.id)
        .where(
            GenerationJob.project_id == run.project_id,
            GenerationJob.input_snapshot["harness_origin"]["run_id"].astext == str(run.id),
            GenerationJob.input_snapshot["harness_origin"]["task_id"].astext == str(task.id),
        )
        .limit(1)
    )
    creation = db.scalar(
        select(Creation.id)
        .where(
            Creation.project_id == run.project_id,
            Creation.created_by == run.created_by,
            Creation.request_key == attempt.id,
        )
        .limit(1)
    )
    if existing or creation:
        return None
    return {
        "kind": "tool",
        "tool": "media.generate",
        "arguments": copy.deepcopy(attempt.operation["arguments"]),
    }


def error_view(db, run, task):
    error = dict(task.error or {})
    if error.get("code") == "QUOTA_EXCEEDED":
        error["retryable"] = blocked_generation_action(
            db, run, task
        ) is not None and task.state.get("retries", 0) < run.policy.get("max_retries", 2)
    return error
