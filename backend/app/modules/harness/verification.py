"""Deterministic result checks. A model assertion is never an artifact receipt."""

import uuid

from pydantic import ValidationError

from app.core.errors import Conflict
from app.models.engineering import EngineeringChange
from app.models.generation import GenerationJob


class ArtifactValidationRequired(Conflict):
    def __init__(self, outputs):
        super().__init__("交付文件需要按本步骤的要求进行技术检查")
        self.references = [output["reference"] for output in outputs]


def job_result(db, ctx, job_id):
    job = db.get(GenerationJob, uuid.UUID(str(job_id)), populate_existing=True)
    if not job or job.project_id != ctx.project.id:
        raise Conflict("生成任务不属于当前项目")
    from app.modules.harness.media_tools import observe_job

    return observe_job(db, ctx, job, check_storage=False)


def _select_artifacts(available, requested):
    from app.modules.harness.context import ContextReference

    if not requested:
        return list(available.values())
    selected = {}
    for item in requested:
        try:
            ref = ContextReference.model_validate(item.get("reference", item))
        except (ValidationError, AttributeError):
            raise Conflict("交付选择必须包含固定生成记录 ID 和文件摘要") from None
        if (
            ref.kind != "generation"
            or not ref.expected_blob_hash
            or ref.start_ms is not None
            or ref.region is not None
        ):
            raise Conflict("交付选择必须引用完整固定生成文件，不能用未渲染的区间或区域代替")
        key = (str(ref.id), ref.expected_blob_hash)
        if key not in available:
            raise Conflict("选中的交付产物不在当前任务及明确依赖的真实成果中")
        selected[key] = available[key]
    return list(selected.values())


def artifact_outputs(db, ctx, receipts, artifact_refs=None):
    """Resolve selected immutable output identities using real successful jobs."""
    available, jobs = {}, set()
    for receipt in receipts:
        data = receipt.get("data")
        if receipt.get("status") != "succeeded" or not isinstance(data, dict):
            continue
        job_id = data.get("job_id") or data.get("preview_job_id")
        if job_id and str(job_id) not in jobs:
            jobs.add(str(job_id))
            actual = job_result(db, ctx, job_id)
            if actual["status"] == "succeeded" and actual["records_ready"]:
                for output in actual["outputs"]:
                    ref = output["reference"]
                    available[(ref["id"], ref["expected_blob_hash"])] = output
    selected = _select_artifacts(available, artifact_refs)
    if not selected:
        raise Conflict("尚无通过实际产物检查的结果，提交任务或文字说明不能视为制作完成")
    return selected


def verify_output(
    db,
    ctx,
    expected,
    receipts,
    *,
    work_id=None,
    requirements=None,
    artifact_refs=None,
    validation_receipts=None,
):
    if artifact_refs and expected != "artifact":
        raise Conflict("只有实际产物交付步骤才能选择交付文件")
    if expected == "reply":
        return []
    verified = []
    for receipt in receipts:
        data = receipt.get("data")
        if receipt.get("status") != "succeeded" or not isinstance(data, dict):
            continue
        if expected == "observation" and receipt.get("tool") == "media.inspect":
            if data.get("coverage"):
                verified.append({"kind": "media_observation", "coverage": data["coverage"]})
        elif expected == "observation":
            from app.modules.tools.capabilities import get_capability

            if get_capability(receipt.get("tool")).effect == "read":
                verified.append(
                    {"kind": "tool_observation", "tool": receipt["tool"], "content_verified": False}
                )
        if (
            expected == "candidate"
            and receipt.get("tool")
            in {"engineering.propose", "engineering.compose", "engineering.selective_edit"}
            and (data.get("id") or data.get("change_id"))
        ):
            change = db.get(EngineeringChange, uuid.UUID(str(data.get("change_id") or data["id"])))
            if change and str(change.work_id) == str(work_id) and change.status != "rejected":
                verified.append(
                    {
                        "kind": "engineering_candidate",
                        "id": str(change.id),
                        "base_revision": change.base_revision,
                        "fingerprint": change.fingerprint,
                    }
                )
    if expected == "artifact":
        from app.modules.harness.technical_validation import prepare_validation, receipt_matches

        # Select only from freshly resolved receipts BEFORE applying final-film
        # requirements; intermediate pictures/audio are not the selected film.
        outputs = artifact_outputs(db, ctx, receipts, artifact_refs)
        missing = False
        for output in outputs:
            prepared = prepare_validation(db, ctx, output, expected=requirements)
            check = next(
                (r for r in validation_receipts or [] if receipt_matches(prepared, r)),
                prepared.cached_receipt,
            )
            if check is None:
                missing = True
                continue
            if check["status"] != "passed":
                raise Conflict("所选交付文件尚未通过本步骤要求的完整技术检查")
            verified.append({**output, "technical_validation": check})
        if missing:
            raise ArtifactValidationRequired(outputs)
    if not verified:
        raise Conflict("尚无通过实际产物检查的结果，提交任务或文字说明不能视为制作完成")
    return verified
