"""Evidence-bound creative assessments; technical validation and human choice stay separate.

The read-only report is persisted by the caller's normal tool receipt. It is not
an objective artistic score, an adoption operation, or proof that sampled frames
cover an entire video. No model or media provider is called by this module.
"""

import hashlib
import json
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import Conflict, Forbidden
from app.models.engineering import EngineeringChange
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionAttempt, ProductionRun, ProductionTask
from app.modules.harness.context import ContextReference, effective_facts, revalidate_context
from app.modules.harness.media_context import describe_media
from app.modules.harness.technical_validation import cached_validation
from app.modules.tools import capabilities, registry
from app.modules.tools.capabilities import ToolPolicy


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Criterion(Strict):
    id: str = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=2000)
    kind: Literal["visual", "audio", "transcript", "continuity", "motion"]
    required: bool = True
    coverage: Literal["complete", "sampled"] = "complete"
    media_types: list[Literal["image", "audio", "video"]] | None = Field(
        default=None, min_length=1, max_length=3
    )
    decision_ids: list[str] = Field(default_factory=list, max_length=20)


class QualityRequirements(Strict):
    criteria: list[Criterion] = Field(default_factory=list, max_length=40)
    require_human_review: bool = False

    @model_validator(mode="after")
    def unique(self):
        if len({item.id for item in self.criteria}) != len(self.criteria):
            raise ValueError("质量要求标识不能重复")
        return self


class Finding(Strict):
    criterion_id: str = Field(min_length=1, max_length=100)
    outcome: Literal["met", "not_met", "not_checked"]
    rationale: str = Field(min_length=1, max_length=4000)
    artifact_indices: list[Annotated[int, Field(strict=True, ge=0, le=31)]] = Field(
        default_factory=list, max_length=32
    )
    observation_ids: list[uuid.UUID] = Field(default_factory=list, max_length=64)


class LocalException(Strict):
    criterion_id: str = Field(min_length=1, max_length=100)
    artifact_index: int = Field(strict=True, ge=0, le=31)
    decision_id: str = Field(min_length=1, max_length=160)


class QualityReport(Strict):
    artifacts: list[ContextReference] = Field(min_length=1, max_length=32)
    findings: list[Finding] = Field(default_factory=list, max_length=40)
    local_exceptions: list[LocalException] = Field(default_factory=list, max_length=32)
    candidate_id: uuid.UUID | None = None
    candidate_fingerprint: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def fixed(self):
        if any(ref.kind == "document" or not ref.expected_blob_hash for ref in self.artifacts):
            raise ValueError("质量检查必须引用固定媒体及文件摘要")
        if len({item.criterion_id for item in self.findings}) != len(self.findings):
            raise ValueError("同一质量要求只能提交一项结论")
        if bool(self.candidate_id) != bool(self.candidate_fingerprint):
            raise ValueError("人工审阅关联必须同时固定候选及指纹")
        for item in self.findings:
            if len(set(item.artifact_indices)) != len(item.artifact_indices) or any(
                type(index) is not int or not 0 <= index < len(self.artifacts)
                for index in item.artifact_indices
            ):
                raise ValueError("质量检查的素材位置无效")
        if len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False)) > 12000:
            raise ValueError("单次质量报告过大，请精简判断说明和重复证据")
        identities = [
            (ref.kind, ref.id, ref.instance_id, ref.start_ms, ref.end_ms, str(ref.region))
            for ref in self.artifacts
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("质量报告不能重复引用相同产物位置")
        return self


class CheckArgs(Strict):
    report: QualityReport


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _media_identity(reference):
    return reference["kind"], str(reference["id"]), reference["expected_blob_hash"]


def _receipt(db, run, consumer, ident, tool, allowed_ids=None):
    if allowed_ids is None:
        from app.modules.harness.evidence import evidence_tasks

        allowed_ids = {row.id for row in evidence_tasks(db, run, consumer)}
    attempt = db.get(ProductionAttempt, uuid.UUID(str(ident)), populate_existing=True)
    task = db.get(ProductionTask, attempt.task_id, populate_existing=True) if attempt else None
    receipt = attempt.result or {} if attempt else {}
    if (
        not attempt
        or not task
        or task.run_id != run.id
        or task.id not in allowed_ids
        or attempt.status != "succeeded"
        or task.status in {"superseded", "canceled", "failed"}
        or receipt.get("status") != "succeeded"
        or receipt.get("tool") != tool
        or not isinstance(receipt.get("data"), dict)
    ):
        raise Conflict("质量检查引用的真实收据不存在、已失效或不属于当前制作任务")
    cutoff = task.state.get("evidence_invalid_before")
    if cutoff:
        try:
            stale = attempt.created_at < datetime.fromisoformat(cutoff)
        except (ValueError, TypeError):
            raise Conflict("任务证据边界无效，需要重新检查") from None
        if stale:
            raise Conflict("这份观察或质量报告早于本次调整，不能作为新方向的验收依据")
    return receipt["data"]


def _applies(criterion, artifact):
    kinds = criterion.media_types or (
        ["image", "video"]
        if criterion.kind in {"visual", "continuity", "motion"}
        else ["audio", "video"]
    )
    return artifact["media_type"] in kinds


def _range(reference, metadata):
    return reference.get("start_ms") or 0, reference.get("end_ms") or metadata.get("duration_ms")


def _covers(ranges, start, end):
    if end is None:
        return False
    cursor = start
    for item in sorted(ranges, key=lambda item: item["start_ms"]):
        if item["start_ms"] > cursor:
            return False
        cursor = max(cursor, item["end_ms"])
        if cursor >= end:
            return True
    return False


def _observed(kind, policy, artifact, observations):
    ref, meta = artifact["reference"], artifact["metadata"]
    rows = [
        item for item in observations if _media_identity(item["reference"]) == _media_identity(ref)
    ]
    start, end = _range(ref, meta)
    if kind == "motion":
        return False, "当前抽帧和短音轨检查不能证明连续动作质量"
    if kind in {"visual", "continuity"}:
        if artifact["media_type"] == "image":
            wanted_region = ref.get("region")
            covered = any(
                "image" in row.get("coverage", {})
                and row["coverage"]["image"].get("region") in (None, wanted_region)
                for row in rows
            )
            return covered, "缺少该图片或所选区域的真实视觉观察"
        if artifact["media_type"] == "video" and policy == "sampled":
            covered = any(
                any(start <= frame < end for frame in row.get("coverage", {}).get("frames_ms", []))
                and row.get("coverage", {}).get("region") in (None, ref.get("region"))
                for row in rows
                if end is not None
            )
            return covered, "缺少指定视频范围内的真实抽帧观察"
        return False, "视频抽样不能满足完整画面检查，请保留未检查状态"
    key = "audio_ranges" if kind == "audio" else "transcript_ranges"
    ranges = [part for row in rows for part in row.get("coverage", {}).get(key, [])]
    if policy == "complete":
        return _covers(ranges, start, end), "声音或转写未覆盖完整指定范围"
    return any(
        part["end_ms"] > start and (end is None or part["start_ms"] < end) for part in ranges
    ), "缺少对应声音或转写证据"


def _human_review(db, run, report):
    value = {"status": "pending", "adopted": False, "candidate_id": None}
    if not report.candidate_id:
        return value
    change = db.get(EngineeringChange, report.candidate_id, populate_existing=True)
    if (
        not change
        or str(change.work_id) != str(run.work_id)
        or change.fingerprint != report.candidate_fingerprint
    ):
        raise Conflict("人工审阅关联的工程候选不匹配")
    for ref in report.artifacts:
        generation = db.get(Generation, ref.id) if ref.kind == "generation" else None
        job = db.get(GenerationJob, generation.job_id) if generation else None
        if (
            not generation
            or not job
            or job.project_id != run.project_id
            or generation.output_blob_hash != ref.expected_blob_hash
            or job.input_snapshot.get("engineering_change_id") != str(change.id)
        ):
            raise Conflict("检查产物不是所选工程候选的固定预览，不能借用其他采用决定")
    return {
        "status": "accepted"
        if change.status == "accepted"
        else "rejected"
        if change.status == "rejected"
        else "pending",
        "adopted": change.status == "accepted",
        "candidate_id": str(change.id),
        "fingerprint": change.fingerprint,
        "committed_revision": change.committed_revision,
    }


def _evaluate(db, ctx, run, task, report, report_id):
    requirements = QualityRequirements.model_validate(
        task.specification.get("quality_requirements") or {}
    )
    criteria = {item.id: item for item in requirements.criteria}
    findings = {item.criterion_id: item for item in report.findings}
    if set(findings) - set(criteria):
        raise Conflict("质量报告不能添加或替换计划中未固定的验收要求")
    artifacts = [describe_media(db, ctx, ref) for ref in report.artifacts]
    active, _ = effective_facts(run.context.get("facts", []))
    decisions = {fact.id: fact for fact in active if fact.kind == "user_decision"}
    exceptions = {}
    for exception in report.local_exceptions:
        fact = decisions.get(exception.decision_id)
        if (
            not fact
            or exception.criterion_id not in criteria
            or exception.artifact_index >= len(artifacts)
        ):
            raise Conflict("局部例外必须来自仍有效的用户决定")
        evidence = fact.evidence.get("quality_exception", {})
        expected_ref = artifacts[exception.artifact_index]["reference"]
        if (
            evidence.get("criterion_id") != exception.criterion_id
            or evidence.get("reference") != expected_ref
        ):
            raise Conflict("局部例外未明确绑定该质量要求和固定素材，不能自动扩大")
        exceptions[(exception.criterion_id, exception.artifact_index)] = fact.id
    from app.modules.harness.evidence import evidence_tasks

    allowed_ids = {row.id for row in evidence_tasks(db, run, task)}
    observations = {}
    for finding in report.findings:
        for ident in finding.observation_ids:
            if ident in observations:
                continue
            value = _receipt(db, run, task, ident, "media.inspect", allowed_ids)
            # A valid ID is not visual evidence. Reauthorize fixed bytes and keep actual coverage.
            if not isinstance(value.get("reference"), dict):
                raise Conflict("观察收据不包含固定的媒体引用")
            fixed = ContextReference.model_validate(value["reference"])
            describe_media(db, ctx, fixed)
            observations[ident] = value
    checks = []
    for criterion in requirements.criteria:
        finding = findings.get(criterion.id)
        issues = []
        applicable = [
            index for index, artifact in enumerate(artifacts) if _applies(criterion, artifact)
        ]
        indices = finding.artifact_indices if finding and finding.artifact_indices else applicable
        if not applicable:
            issues.append("交付中没有与该要求匹配的媒体类型")
        # Scope is fixed by the plan, not narrowed by the checking agent.
        if set(indices) != set(applicable):
            issues.append("检查尚未覆盖该要求对应的每个产物，或包含无关媒体")
        if not finding:
            issues.append("这项质量要求尚未检查")
        if any(ident not in decisions for ident in criterion.decision_ids):
            issues.append("引用的用户要求已失效，需要重新评估当前要求")
        if criterion.kind == "continuity" and len(applicable) < 2:
            issues.append("跨镜头一致性至少需要两个按顺序排列的固定片段")
        evidence_rows = (
            [observations[ident] for ident in finding.observation_ids] if finding else []
        )
        for index in indices:
            if (criterion.id, index) in exceptions:
                continue
            covered, reason = _observed(
                criterion.kind, criterion.coverage, artifacts[index], evidence_rows
            )
            if not covered:
                issues.append(f"素材 {index + 1}：{reason}")
        outcome = "not_checked" if issues else finding.outcome
        checks.append(
            {
                "criterion_id": criterion.id,
                "instruction": criterion.instruction,
                "kind": criterion.kind,
                "coverage_policy": criterion.coverage,
                "required": criterion.required,
                "outcome": outcome,
                "claimed_outcome": finding.outcome if finding else "not_checked",
                "rationale": finding.rationale if finding else "未提交检查结果",
                "issues": issues,
                "observations": [
                    {
                        "attempt_id": str(ident),
                        "reference": observations[ident]["reference"],
                        "coverage": observations[ident].get("coverage", {}),
                    }
                    for ident in (finding.observation_ids if finding else [])
                ],
                "adjacent_pairs": [
                    [left, right] for left, right in zip(applicable, applicable[1:], strict=False)
                ]
                if criterion.kind == "continuity"
                else [],
                "user_exceptions": [
                    {"artifact_index": index, "decision_id": value}
                    for (key, index), value in exceptions.items()
                    if key == criterion.id
                ],
            }
        )
    technical = []
    for artifact in artifacts:
        ref = artifact["reference"]
        # Technical acceptance applies to full delivered files, never just a selected region.
        if ref.get("start_ms") is not None or ref.get("region"):
            checked = None
        else:
            checked = cached_validation(
                db,
                ctx,
                ref,
                expected=task.specification.get("output_requirements"),
                prior_receipt=next(
                    (
                        r
                        for r in task.state.get("finish_technical_checks", [])
                        if r.get("blob_hash") == ref.get("expected_blob_hash")
                    ),
                    None,
                ),
            )
        technical.append(
            {
                "reference": ref,
                "status": checked["status"] if checked else "not_checked",
                "receipt": checked,
            }
        )
    creative_ready = all(check["outcome"] == "met" for check in checks if check["required"])
    technical_ready = all(check["status"] == "passed" for check in technical)
    human = _human_review(db, run, report)
    human_ready = not requirements.require_human_review or human["status"] == "accepted"
    return {
        "report_id": str(report_id),
        "version": 1,
        "requirements_hash": _digest(requirements.model_dump(mode="json")),
        "report": report.model_dump(mode="json"),
        "technical": {"ready": technical_ready, "checks": technical},
        "creative_assessment": {
            "ready": creative_ready,
            "kind": "agent_assessment_with_observation_evidence",
            "checks": checks,
            "objective_score": None,
        },
        "human_review": {**human, "required": requirements.require_human_review},
        "ready": technical_ready and creative_ready and human_ready,
        "adopted": human["adopted"],
        "limitations": [
            "创意判断是建议，真实观察收据不保证审美判断正确",
            "抽样检查不代表整段影片质量",
            "该工具不采用或替换任何成果",
        ],
    }


def check(db, ctx, context, args):
    ctx = revalidate_context(db, ctx)
    grant = context.input_context.get("execution", {})
    try:
        run = db.get(ProductionRun, uuid.UUID(grant["run_id"]), populate_existing=True)
        task = db.get(ProductionTask, uuid.UUID(grant["task_id"]), populate_existing=True)
        report_id = uuid.UUID(grant["request_key"])
    except (ValueError, KeyError, TypeError):
        raise Forbidden("质量检查需要系统注入的当前任务身份") from None
    if (
        not run
        or not task
        or run.project_id != ctx.project.id
        or run.created_by != ctx.user.id
        or task.run_id != run.id
        or task.status in {"superseded", "canceled", "failed"}
    ):
        raise Forbidden("质量检查不属于当前可执行任务")
    value = _evaluate(db, ctx, run, task, args.report, report_id)
    # Preserve the complete bounded recipe for later verification. Summaries do
    # not repeat full source references, technical receipts or model rationale.
    value["technical"]["checks"] = [
        {"artifact_index": index, "status": item["status"]}
        for index, item in enumerate(value["technical"]["checks"])
    ]
    value["creative_assessment"]["checks"] = [
        {
            "criterion_id": item["criterion_id"],
            "outcome": item["outcome"],
            "issues": item["issues"][:1],
            "issue_count": len(item["issues"]),
            "adjacent_pairs": item["adjacent_pairs"],
            "observation_count": len(item["observations"]),
            "user_exceptions": item["user_exceptions"],
        }
        for item in value["creative_assessment"]["checks"]
    ]
    if len(json.dumps(value, ensure_ascii=False)) > 22500:
        # Still auditable: complete recipe and receipt IDs remain above; callers
        # can re-run a focused check for details without silently losing evidence.
        for item in value["creative_assessment"]["checks"]:
            item.pop("adjacent_pairs")
            item.pop("user_exceptions")
            item["issues"] = [issue[:120] for issue in item["issues"]]
        value["details_compacted"] = True
    if len(json.dumps(value, ensure_ascii=False)) > 23000:
        raise Conflict("质量报告超出可持久化大小，请精简说明和重复证据")
    return value


def verify_quality(db, ctx, run, task, report_id, verified_outputs):
    """Finish gate. Recompute against current evidence and decisions, not cached ready=True."""
    ctx = revalidate_context(db, ctx)
    requirements = QualityRequirements.model_validate(
        task.specification.get("quality_requirements") or {}
    )
    if not requirements.criteria and not requirements.require_human_review:
        return None
    if not report_id:
        raise Conflict("尚未提交带真实观察证据的质量检查报告")
    stored = _receipt(db, run, task, report_id, "quality.check")
    if stored.get("requirements_hash") != _digest(requirements.model_dump(mode="json")):
        raise Conflict("质量要求已经变化，需要重新检查")
    report = QualityReport.model_validate(stored["report"])
    outputs = [value.get("reference", value) for value in verified_outputs]
    actual = {
        _media_identity(value)
        for value in outputs
        if isinstance(value, dict) and value.get("expected_blob_hash")
    }
    if not actual or actual != {
        _media_identity(ref.model_dump(mode="json")) for ref in report.artifacts
    }:
        raise Conflict("质量报告与本次实际交付产物不一致")
    result = _evaluate(db, ctx, run, task, report, report_id)
    if not result["ready"]:
        raise Conflict(
            "交付验收尚未通过：仍有未检查、不满足的要求或未完成的人工审阅", {"quality": result}
        )
    return result


def register_tools():
    if "quality.check" in registry.CATALOG:
        return
    registry.register(
        registry.Tool(
            "quality.check",
            "根据真实媒体观察收据整理质量检查。必须逐项对应已确认要求，明确抽样范围、未检查项、用户局部例外和人工采用状态；不评分或采用成果。",
            CheckArgs,
            group="quality",
        ),
        check,
    )
    capabilities.register_contract(
        "quality.check",
        ToolPolicy(
            effect="read",
            idempotency="read_only",
            cancellation="not_applicable",
            recovery="repeat_read",
            output_kind="quality_report",
        ),
    )
