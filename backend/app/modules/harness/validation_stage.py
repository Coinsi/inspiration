"""Technical delivery worker: detached decoding, then lease-fenced publication."""

import copy
import uuid

from app.core.errors import Conflict
from app.models.generation import GenerationJob
from app.models.harness import ProductionRun, ProductionTask
from app.modules.harness import service
from app.modules.harness.technical_validation import (
    prepare_validation,
    record_validation,
    validate_prepared,
)


def execute_finish_validation(ticket, sessions):
    """Validate a fixed final selection against its coordinator's requirements."""
    from app.modules.harness.evidence import collect_receipts
    from app.modules.harness.runtime import _get_locked
    from app.modules.harness.schemas import FinishAction
    from app.modules.harness.verification import artifact_outputs

    with sessions() as db:
        run = db.get(ProductionRun, uuid.UUID(ticket["run_id"]))
        task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        ctx = service.execution_context(db, run)
        request = copy.deepcopy(task.state["validation_finish"])
        action = FinishAction.model_validate(request["action"])
        if request["requirements"] != task.specification.get("output_requirements", {}):
            raise Conflict("交付验收要求已改变，请重新确认最终产物")
        outputs = artifact_outputs(db, ctx, collect_receipts(db, run, task), action.artifacts)
        prepared = [
            prepare_validation(db, ctx, output, expected=request["requirements"])
            for output in outputs
        ]
        prior = task.state.get("finish_technical_checks", [])

    def canceled():
        with sessions() as db:
            current = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
            return (
                not current
                or current.status != "running"
                or str(current.lease_token) != ticket["lease_token"]
            )

    checks = [
        validate_prepared(
            item,
            prior_receipt=next((r for r in prior if r.get("blob_hash") == item.blob_hash), None),
            canceled=canceled,
        )
        for item in prepared
    ]
    with sessions() as db:
        locked = _get_locked(db, ticket)
        if not locked:
            service.finish_attempt(db, ticket, result={"technical_checks": checks})
            db.commit()
            return
        run, task, _ = locked
        ctx = service.execution_context(db, run)
        if (
            task.state.get("validation_finish") != request
            or task.specification.get("output_requirements", {}) != request["requirements"]
        ):
            raise Conflict("验收期间交付范围或要求已改变，请按当前任务重新检查")
        # Edges may have been invalidated while the decoder was working. The
        # completed physical check never restores authority to obsolete outputs.
        artifact_outputs(db, ctx, collect_receipts(db, run, task), action.artifacts)
        checks = [
            record_validation(db, ctx, item, check)
            for item, check in zip(prepared, checks, strict=True)
        ]
        passed = all(check["status"] == "passed" for check in checks)
        state = copy.deepcopy(task.state)
        state.pop("validation_finish", None)
        state["finish_technical_checks"] = checks
        if passed:
            # Replay is persisted: after a crash or pause no model has to repeat
            # its choice, and Finish still performs the creative/human gates.
            state["pending_action"] = action.model_dump(mode="json")
        error = (
            None
            if passed
            else {
                "code": "TECHNICAL_VALIDATION_FAILED",
                "message": "所选交付文件不符合本步骤的技术要求，成果已保留；不会重新生成或扣费",
                "retryable": False,
            }
        )
        service.finish_attempt(
            db,
            ticket,
            status="queued" if passed else "failed",
            state=state,
            result={"technical_checks": checks},
            error=error,
        )
        from app.modules.conversation.production import publish

        publish(db, run)
        db.commit()


def execute_validation(ticket, sessions):
    from app.modules.harness.media_tools import observe_job
    from app.modules.harness.runtime import _get_locked

    with sessions() as db:
        run = db.get(ProductionRun, uuid.UUID(ticket["run_id"]))
        task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        ctx = service.execution_context(db, run)
        job = db.get(GenerationJob, uuid.UUID(task.state["validation_job_id"]))
        if not job or job.project_id != run.project_id or job.created_by != run.created_by:
            raise Conflict("无法核验本次任务的成果来源")
        receipt = {
            "tool": "media.job_observe",
            "status": "succeeded",
            "data": observe_job(db, ctx, job, check_storage=False),
        }
        data = receipt["data"]
        if job.status != "succeeded" or not data["outputs"]:
            raise Conflict("任务尚未产生可验收的成果")
        prepared = [
            prepare_validation(
                db, ctx, output, expected=task.specification.get("output_requirements", {})
            )
            for output in data["outputs"]
        ]
        job_id = str(job.id)

    # No DB transaction is held while decoding. Cancellation prevents publication
    # through the existing lease fence even if an external decoder finishes late.
    def canceled():
        with sessions() as db:
            task = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
            return (
                not task
                or task.status != "running"
                or str(task.lease_token) != ticket["lease_token"]
            )

    checks = [validate_prepared(item, canceled=canceled) for item in prepared]
    with sessions() as db:
        locked = _get_locked(db, ticket)
        if not locked:
            service.finish_attempt(
                db, ticket, result={"technical_checks": checks, "job_id": job_id}
            )
            db.commit()
            return
        run, task, _ = locked
        from app.modules.harness.resources import settle_media

        settle_media(db, run, uuid.UUID(job_id))
        ctx = service.execution_context(db, run)
        checks = [
            record_validation(db, ctx, item, check)
            for item, check in zip(prepared, checks, strict=True)
        ]
        passed = all(check["status"] == "passed" for check in checks)
        for output, check in zip(data["outputs"], checks, strict=True):
            output["technical_validation"] = check
            output["storage_accessible"] = check["status"] == "passed"
        data["materialized"] = passed
        data["technical_validated"] = passed
        data["unverified"] = (
            ["creative_quality"] if passed else ["technical_validation", "creative_quality"]
        )
        state = copy.deepcopy(task.state)
        state.pop("validation_job_id", None)
        state.pop("waiting_job_id", None)
        state["job_receipt"] = receipt
        state["technical_checks"] = checks
        state["steps"] = [*state.get("steps", []), {"receipt": receipt}][-20:]
        if passed:
            status, error = ("succeeded" if task.kind == "tool" else "queued"), None
        else:
            status, error = (
                "failed",
                {
                    "code": "TECHNICAL_VALIDATION_FAILED",
                    "message": "成果未通过技术验收，请查看检查记录；不会自动重新生成或扣费",
                    "retryable": False,
                    "job_id": job_id,
                },
            )
        service.finish_attempt(db, ticket, status=status, state=state, result=receipt, error=error)
        from app.modules.conversation.production import publish

        publish(db, run)
        db.commit()
