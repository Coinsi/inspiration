"""Live authorization at production job side-effect boundaries.

Snapshots preserve intent, not permission. No agent kernel or in-memory role is
trusted here. The helper reads metadata only and never commits, invokes providers,
creates resources, or adopts outputs. Callers release reads before slow I/O.
"""

import uuid
from types import SimpleNamespace

from app.core.errors import Conflict, Forbidden
from app.core.permissions import require
from app.models.asset import Asset
from app.models.creation import Creation
from app.models.engineering import EngineeringChange
from app.models.generation import Generation
from app.models.harness import ProductionRun, ProductionTask
from app.models.library import LibraryMedia, MediaVersion
from app.models.shot import Shot
from app.models.timeline import Timeline
from app.models.work import CreativeWork
from app.modules.harness.context import revalidate_context

_TARGETS = {
    "asset": Asset,
    "creation": Creation,
    "shot": Shot,
    "timeline": Timeline,
    "work": CreativeWork,
}
_EFFECTS = {
    "media.generate": "generate",
    "media.process": "candidate",
    "engineering.preview": "preview",
}


def _target(db, ctx, kind, ident):
    cls = _TARGETS.get(kind)
    row = db.get(cls, ident, populate_existing=True) if cls else None
    if not row or row.project_id != ctx.project.id or getattr(row, "deleted_at", None):
        raise Forbidden("制作目标已不可用，停止执行和结果发布")
    return row


def _reference(db, ctx, kind, ident, blob_hash):
    """Refresh source identity, including soft-deleted library and generation owners."""
    from app.modules.harness.media_context import describe_media

    ident = uuid.UUID(str(ident))
    if kind == "media_version":
        row = db.get(MediaVersion, ident, populate_existing=True)
        if row:
            db.get(LibraryMedia, row.media_id, populate_existing=True)
    elif kind == "generation":
        row = db.get(Generation, ident, populate_existing=True)
        if row:
            _target(db, ctx, row.target_type, row.target_id)
    else:
        raise Forbidden("制作任务缺少可核对的固定媒体来源")
    if not blob_hash:
        raise Forbidden("制作任务缺少固定媒体摘要")
    return describe_media(
        db, ctx, {"kind": kind, "id": str(ident), "expected_blob_hash": blob_hash}
    )


def _run(db, ctx, job, snapshot, origin, tool, *, receiving):
    if not origin.get("run_id") and not origin.get("task_id"):
        # Local deterministic tools retain their ordinary non-agent business API.
        if tool != "media.process" or job.provider != "local":
            raise Forbidden("制作任务缺少持久执行归属")
        return
    run = db.get(ProductionRun, uuid.UUID(str(origin.get("run_id"))), populate_existing=True)
    task = db.get(ProductionTask, uuid.UUID(str(origin.get("task_id"))), populate_existing=True)
    if (
        not run
        or not task
        or task.run_id != run.id
        or run.created_by != ctx.user.id
        or run.project_id != ctx.project.id
        or (not receiving and run.status in {"canceled", "superseded", "failed", "succeeded"})
        or (not receiving and task.status in {"canceled", "superseded", "failed", "succeeded"})
        or (origin.get("plan_revision") is not None and task.revision != origin["plan_revision"])
    ):
        raise Forbidden("制作任务已撤销或归属已变化，停止执行和结果发布")
    if not receiving and _EFFECTS[tool] not in run.policy.get("effects", []):
        raise Forbidden("本次制作已不再授权此工具的副作用")
    allowed = task.specification.get("allowed_tools")
    if not receiving and allowed is not None and tool not in allowed:
        raise Forbidden("此工作步骤不再允许执行该工具")
    work_id = snapshot.get("engineering_work_id") or snapshot.get("work_id")
    if job.target_type == "work":
        work_id = str(job.target_id)
    if not receiving and run.work_id and str(run.work_id) != str(work_id):
        raise Forbidden("制作任务已不再属于授权作品")
    if tool == "media.generate":
        entry = run.context.get("budget_reservations", {}).get(str(origin["request_key"]))
        if not entry or entry.get("job_id") != str(job.id):
            raise Forbidden("生成任务缺少与本任务一致的费用预留")
    limits = snapshot.get("harness_resource_limits") or {}
    if limits:
        entry = run.context.get("resource_reservations", {}).get(limits.get("reservation_key"))
        if (
            not entry
            or entry.get("job_id") not in {None, str(job.id)}
            or entry.get("reserved", {}).get("output_storage_bytes") != limits.get("output_bytes")
        ):
            raise Forbidden("制作任务的资源预留已失效")


def _engineering_sources(db, ctx, snapshot):
    from app.modules.engineering.documents import closure

    work_id = uuid.UUID(str(snapshot["engineering_work_id"]))
    _target(db, ctx, "work", work_id)
    change = db.get(
        EngineeringChange, uuid.UUID(str(snapshot["engineering_change_id"])), populate_existing=True
    )
    if not change or change.work_id != work_id or change.status == "rejected":
        raise Forbidden("工程候选已不可用，停止渲染与结果发布")
    sources = list(change.snapshot.get("resources", []))
    if change.snapshot.get("manifest_ref"):
        sources.extend(
            node["payload"]
            for node in closure(db, work_id, change.snapshot["manifest_ref"])
            if node["kind"] == "resource"
        )
    seen = set()
    for item in sources:
        key = (item["source_kind"], item["source_id"], item["blob_hash"])
        if key in seen:
            continue
        seen.add(key)
        _reference(
            db, ctx, "media_version" if key[0] == "library" else "generation", key[1], key[2]
        )
    # Legacy editable timeline documents also freeze their generation references.
    if snapshot.get("operation") == "render":
        opts = snapshot.get("options", {})
        for item in [*snapshot.get("clips", []), *opts.get("audio", []), *opts.get("visuals", [])]:
            if item.get("generation_id"):
                _reference(db, ctx, "generation", item["generation_id"], item.get("blob_hash"))


def validate_job(db, job, snapshot=None, *, phase="submit"):
    """Revalidate an application-owned job before provider, CAS and publication.

    Accepted paid work may finish into an unselected immutable candidate after
    plan cancellation/supersession. Live actor or source revocation still forbids
    CAS and publication; no current task/engineering selection is advanced here.
    Legacy jobs without harness_origin keep their
    existing API behavior; malformed new provenance is rejected, not bypassed.
    """
    snapshot = snapshot if snapshot is not None else job.input_snapshot or {}
    if "harness_origin" not in snapshot:
        return
    origin = snapshot["harness_origin"]
    if not isinstance(origin, dict) or not origin.get("request_key"):
        raise Forbidden("制作任务执行来源无效")
    uuid.UUID(str(origin["request_key"]))
    tool = origin.get("tool", "media.generate")
    if tool not in _EFFECTS:
        raise Forbidden("制作任务工具来源不受支持")
    ctx = revalidate_context(
        db,
        SimpleNamespace(
            project=SimpleNamespace(id=job.project_id), user=SimpleNamespace(id=job.created_by)
        ),
    )
    require(ctx.role, "engineering.edit" if tool == "engineering.preview" else "generation.trigger")
    if tool == "media.process":
        require(ctx.role, "engineering.edit")
    _target(db, ctx, job.target_type, job.target_id)
    if phase not in {"submit", "receipt", "resume"}:
        raise Forbidden("未知的制作执行阶段")
    receiving = phase != "submit"
    if receiving and (
        tool != "media.generate" or job.provider == "local" or not job.external_job_id
    ):
        raise Forbidden("只有已取得上游句柄的生成任务可以接收旧结果")
    if phase == "resume":
        from app.modules.harness.recovery_tools import validate_resume_guard

        if not (job.routing_state or {}).get("harness_receipt_recovery"):
            raise Forbidden("旧任务恢复缺少明确的仅接收授权")
        validate_resume_guard(db, job)
    _run(db, ctx, job, snapshot, origin, tool, receiving=receiving)
    if tool == "media.process":
        if job.provider != "local" or snapshot.get("operation") not in {
            "refine",
            "video_frames",
            "video_trim",
            "video_audio",
        }:
            raise Forbidden("本地处理任务不能切换到未授权操作或供应商")
        ref = snapshot.get("source_reference", {})
        if ref.get("expected_blob_hash") != snapshot.get("source_blob"):
            raise Conflict("处理源文件与固定引用不一致")
        _reference(db, ctx, ref.get("kind"), ref.get("id"), ref.get("expected_blob_hash"))
    elif tool == "engineering.preview":
        if job.provider != "local" or snapshot.get("operation") not in {
            "render",
            "engineering_image",
            "engineering_render",
        }:
            raise Forbidden("工程预览不能切换到未授权操作或供应商")
        _engineering_sources(db, ctx, snapshot)
    else:
        if job.provider == "local" or snapshot.get("operation", "generate") != "generate":
            raise Forbidden("媒体生成任务不能切换到未授权本地操作")
        sources, refs = snapshot.get("reference_sources", []), snapshot.get("references", [])
        if len(sources) != len(refs) or snapshot.get("mask"):
            raise Forbidden("生成参考素材缺少可核对的固定来源")
        for source, ref in zip(sources, refs, strict=True):
            from app.modules.generation.references import Reference

            selected = Reference.model_validate(source)
            if source.get("role", "reference") != ref.get("role", "reference"):
                raise Conflict("参考素材用途与固定提交不一致")
            _reference(db, ctx, selected.source_type, selected.source_id, ref["blob_hash"])
    return ctx
