"""Persistent candidate decisions; no model polling and no automatic adoption."""

import copy
import uuid

from sqlalchemy import select

from app.core.errors import Conflict, NotFound
from app.models.engineering import EngineeringChange
from app.models.harness import ProductionTask


def begin(db, ctx, run, task, action):
    from app.modules.engineering.service import work

    if not run.work_id:
        raise Conflict("请先关联候选所属作品")
    work(db, ctx, run.work_id)
    change = db.get(EngineeringChange, action.change_id)
    if not change or change.work_id != run.work_id:
        raise NotFound("工程候选不属于关联作品")
    if change.fingerprint != action.fingerprint:
        raise Conflict("候选版本已变化，请读取固定候选后再等待")
    return {
        **copy.deepcopy(task.state),
        "waiting_candidate": {
            "change_id": str(change.id),
            "work_id": str(change.work_id),
            "fingerprint": change.fingerprint,
            "message": action.message,
        },
    }


def reconcile(db, run):
    """Observe domain decision as a wake-up event. Caller holds run lock."""
    from app.modules.harness import service

    if run.status in {"canceled", "superseded"}:
        return
    for task in db.scalars(
        select(ProductionTask).where(
            ProductionTask.run_id == run.id, ProductionTask.status == "waiting_candidate"
        )
    ):
        target = task.state["waiting_candidate"]
        change = db.get(EngineeringChange, uuid.UUID(target["change_id"]), populate_existing=True)
        if (
            not change
            or change.work_id != run.work_id
            or change.fingerprint != target["fingerprint"]
        ):
            task.status = "failed"
            task.error = {
                "code": "CANDIDATE_CHANGED",
                "message": "等待的候选无法核验",
                "retryable": False,
            }
            continue
        if change.status not in {"accepted", "rejected"}:
            continue
        decision = {
            **target,
            "status": change.status,
            "adopted": change.status == "accepted",
            "base_revision": change.base_revision,
        }
        state = copy.deepcopy(task.state)
        state.pop("waiting_candidate", None)
        state["candidate_decision"] = decision
        task.state, task.status = state, "queued"
        service.event(db, run, "candidate.decided", {"task_id": str(task.id), **decision})
    db.flush()
