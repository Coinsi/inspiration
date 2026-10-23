"""Candidate previews and atomic adoption through the existing timeline writer."""

import copy
import uuid

from sqlalchemy import select

from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.core.permissions import require
from app.models.engineering import EngineeringChange
from app.models.generation import Generation, GenerationJob
from app.models.storage import Blob
from app.modules.engineering import schemas, service, timeline
from app.modules.generation import jobs
from app.modules.timeline import editing
from app.modules.timeline.schemas import TimelineDocumentIn


def propose_timeline(db, ctx, work, candidate, command):
    t = editing.lock(db, ctx.project.id, command.timeline_id)
    if isinstance(command, schemas.RestoreTimeline):
        old = service.snapshot_at(db, work.id, command.engineering_revision)
        captured = old.get("timeline_captures", {}).get(str(t.id))
        if not captured:
            raise NotFound("该工程版本没有这条时间线")
        data = TimelineDocumentIn.model_validate(
            {
                **captured["document"],
                "revision": command.timeline_revision,
            }
        )
    else:
        data = command.document
    from app.modules.engineering.collaboration import timeline_baseline

    before = TimelineDocumentIn.model_validate(
        timeline_baseline(db, ctx, t.id, data.revision)
    ).model_dump(mode="json")
    if data.revision != t.revision:
        data = fixed_timeline_draft(data, before)
    document, frozen = jobs.document_render_snapshot(db, ctx, t.id, data)
    summary = []
    for field, label in (
        ("items", "片段"),
        ("audio", "音轨"),
        ("subtitles", "字幕"),
        ("visuals", "叠加画面"),
    ):
        old = {str(item.get("id")): item for item in before[field]}
        new = {str(item.get("id")): item for item in document[field]}
        added, removed = len(new.keys() - old.keys()), len(old.keys() - new.keys())
        changed = sum(new[key] != old[key] for key in new.keys() & old.keys())
        if added or removed or changed:
            summary.append(f"{label}：新增 {added}，修改 {changed}，移除 {removed}")
        elif list(old) != list(new):
            summary.append(f"{label}：调整顺序")
    timeline.store(candidate, t.id, document, frozen)
    return {
        "timeline_id": str(t.id),
        "expected_revision": data.revision,
        "document": document,
        "render_snapshot": frozen,
        "aspect_ratio": command.aspect_ratio,
        "summary": summary or ["保留当前剪辑内容，建立可审阅版本"],
    }


def fixed_timeline_draft(data, before):
    """Resolve omitted legacy fields against the old revision, never live edits."""
    raw = data.model_dump(mode="json")
    if "visuals" not in data.model_fields_set:
        raw["visuals"] = copy.deepcopy(before.get("visuals", []))
    for field, key in (("items", "shot_id"), ("audio", "generation_id"), ("subtitles", None)):
        used = {item["id"] for item in raw[field] if item.get("id")}
        for item in raw[field]:
            if not item.get("id"):
                previous = next(
                    (
                        old
                        for old in before[field]
                        if old.get("id") not in used
                        and (key is None or old.get(key) == item.get(key))
                    ),
                    None,
                )
                item["id"] = previous["id"] if previous else str(uuid.uuid4())
                used.add(item["id"])
            if field == "items" and not item.get("generation_id"):
                previous = next(
                    (
                        old
                        for old in before[field]
                        if old.get("id") == item["id"] and old.get("shot_id") == item.get("shot_id")
                    ),
                    None,
                )
                if not previous or not previous.get("generation_id"):
                    raise Conflict("旧基线候选必须明确指定画面素材，不能读取镜头的当前选用结果")
                item["generation_id"] = previous["generation_id"]
    return TimelineDocumentIn.model_validate(raw)


def get_change(db, work_id, change_id):
    change = db.scalar(
        select(EngineeringChange).where(
            EngineeringChange.id == change_id,
            EngineeringChange.work_id == work_id,
        )
    )
    if not change:
        raise NotFound("修改候选不存在")
    return change


def preview_query(ctx, change):
    return select(GenerationJob).where(
        GenerationJob.project_id == ctx.project.id,
        GenerationJob.input_snapshot["engineering_change_id"].astext == str(change.id),
        GenerationJob.input_snapshot["engineering_work_id"].astext == str(change.work_id),
    )


def find_preview(db, ctx, change, output_id=None):
    key = GenerationJob.input_snapshot["engineering_output_id"].astext
    return db.scalar(
        preview_query(ctx, change)
        .where(key == output_id if output_id else key.is_(None))
        .order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc())
        .limit(1)
    )


def timeline_updates(change):
    primary = change.timeline_update
    return [primary, *primary.get("additional_updates", [])] if primary else []


def timeline_output_id(change, update):
    return (
        None
        if update["timeline_id"] == change.timeline_update["timeline_id"]
        else "timeline:" + update["timeline_id"]
    )


def preview_info(db, ctx, job):
    output = (
        db.scalar(
            select(Generation).where(
                Generation.job_id == job.id,
                Generation.project_id == ctx.project.id,
            )
        )
        if job and job.status == "succeeded"
        else None
    )
    return (
        {
            "job_id": str(job.id),
            "status": job.status,
            "error": job.error,
            "blob_hash": output.output_blob_hash if output else None,
            "output_type": output.output_type if output else None,
            "output_id": job.input_snapshot.get("engineering_output_id"),
        }
        if job
        else None
    )


def details(db, ctx, change):
    result = service.change_out(change)
    job = db.get(GenerationJob, change.preview_job_id) if change.preview_job_id else None
    if change.timeline_update:
        job = find_preview(db, ctx, change)
        result["preview_job_id"] = str(job.id) if job else None
    result["preview"] = preview_info(db, ctx, job)
    key = GenerationJob.input_snapshot["engineering_output_id"].astext
    latest = db.scalars(
        preview_query(ctx, change)
        .distinct(key)
        .order_by(key, GenerationJob.created_at.desc(), GenerationJob.id.desc())
    )
    result["previews"] = [preview_info(db, ctx, item) for item in latest]
    return result


def preview(db, ctx, work_id, change_id, data, *, execution_snapshot=None):
    require(ctx.role, "engineering.edit")
    service.work(db, ctx, work_id, lock=True)
    change = get_change(db, work_id, change_id)
    if change.fingerprint != data.fingerprint:
        raise Conflict("审阅内容不匹配")
    updates = timeline_updates(change)
    selected = next(
        (
            u
            for u in updates
            if data.output_id in (timeline_output_id(change, u), "timeline:" + u["timeline_id"])
        ),
        None,
    )
    if not selected:
        return preview_native(db, ctx, work_id, change, data, execution_snapshot=execution_snapshot)
    output_id = timeline_output_id(change, selected)
    prior = find_preview(db, ctx, change, output_id)
    if prior and (prior.status not in ("failed", "canceled") or data.retry_job_id != prior.id):
        change.preview_job_id = prior.id
        return service.change_out(change)
    if change.status != "proposed":
        raise Conflict("已采用的候选不能重新生成预览")
    service.validate_sources(db, ctx, work_id, change.snapshot)
    if selected.get("document_ref"):
        from app.modules.engineering.documents import compile_sequence

        frozen, aspect = compile_sequence(db, work_id, selected["document_ref"])
    else:
        frozen = copy.deepcopy(selected["render_snapshot"])
        aspect = selected.get("aspect_ratio", "16:9")
    job = jobs.local_job(
        db,
        ctx,
        "timeline",
        uuid.UUID(selected["timeline_id"]),
        "render",
        {
            **(execution_snapshot or {}),
            "clips": frozen["clips"],
            "options": {
                "height": 720,
                "aspect_ratio": aspect,
                "subtitle_mode": "burn",
                "mute": False,
                **frozen["tracks"],
            },
            "engineering_change_id": str(change.id),
            "engineering_work_id": str(work_id),
            "engineering_output_id": output_id,
        },
        "video",
        defer=True,
    )
    # Persist the job and its candidate link together before dispatch commits/releases locks.
    change.preview_job_id = job.id
    db.flush()
    jobs.dispatch(db, job)
    return service.change_out(change)


def preview_native(db, ctx, work_id, change, data, *, execution_snapshot=None):
    from app.modules.engineering import executors

    if change.status != "proposed":
        raise Conflict("已采用的候选不能重新生成预览")
    roots = change.snapshot.get("native_roots", {})
    output_id = data.output_id
    if output_id is None and len(roots) == 1:
        output_id = next(iter(roots))
    if output_id not in roots:
        raise CapabilityUnsupported("请选择需要预览的工程输出")
    prior = find_preview(db, ctx, change, output_id)
    if prior:
        if prior.input_snapshot.get("engineering_output_id") != output_id:
            if prior.status in jobs.ACTIVE:
                raise Conflict("另一个输出仍在预览，请完成或取消后再切换")
        elif prior.status not in ("failed", "canceled") or data.retry_job_id != prior.id:
            change.preview_job_id = prior.id
            return service.change_out(change)
    service.validate_sources(db, ctx, work_id, change.snapshot)
    plan, output_type = executors.compile_plan(db, work_id, roots[output_id])
    job = jobs.local_job(
        db,
        ctx,
        "work",
        work_id,
        "engineering_render",
        {
            **(execution_snapshot or {}),
            "engineering_plan": plan,
            "engineering_output_id": output_id,
            "engineering_change_id": str(change.id),
            "engineering_work_id": str(work_id),
        },
        output_type,
        defer=True,
    )
    change.preview_job_id = job.id
    db.flush()
    jobs.dispatch(db, job)
    return service.change_out(change)


def adopt_timeline(db, ctx, change, committed_snapshot):
    require(ctx.role, "timeline.edit")
    for update in sorted(timeline_updates(change), key=lambda u: u["timeline_id"]):
        adopt_one_timeline(db, ctx, change, committed_snapshot, update)


def adopt_one_timeline(db, ctx, change, committed_snapshot, update):
    job = find_preview(db, ctx, change, timeline_output_id(change, update))
    if not job or job.status != "succeeded":
        raise Conflict("请先完成候选预览，再确认采用")
    if (job.input_snapshot or {}).get("engineering_change_id") != str(change.id):
        raise Conflict("预览不属于该候选")
    output = db.scalar(
        select(Generation).where(
            Generation.job_id == job.id,
            Generation.project_id == ctx.project.id,
            Generation.output_type == "video",
        )
    )
    if not output or not output.output_blob_hash or not db.get(Blob, output.output_blob_hash):
        raise Conflict("候选预览结果不可用，请重新生成预览")
    tid = uuid.UUID(update["timeline_id"])
    t = editing.lock(db, ctx.project.id, tid)
    if t.revision != update["expected_revision"]:
        raise Conflict("预览后时间线已被修改，旧候选保留，请基于最新时间线重新提出修改")
    editor = update["document"]
    if update.get("document_ref"):
        from app.modules.engineering.documents import compile_sequence, read_revision

        compile_sequence(db, change.work_id, update["document_ref"])
        _, fixed = read_revision(
            db,
            change.work_id,
            uuid.UUID(update["document_ref"]["id"]),
            update["document_ref"]["revision"],
        )
        editor = {**fixed.payload["editor"], "revision": update["expected_revision"]}
    result = editing.save_document(
        db,
        ctx,
        tid,
        TimelineDocumentIn.model_validate(editor),
        label="采用工程候选",
    )
    if update.get("document_ref"):
        from app.models.work import WorkTimeline

        link = db.get(WorkTimeline, (change.work_id, tid))
        if not link:
            link = WorkTimeline(work_id=change.work_id, timeline_id=tid)
            db.add(link)
        link.document_revision = update["document_ref"]["revision"]
    capture = committed_snapshot["timeline_captures"][str(tid)]
    capture["source_revision"] = result["revision"]
    capture["document"] = result


def pin_native_artifacts(db, ctx, change, snapshot):
    """Freeze adopted render identities; later previews cannot change a saved work."""
    previous = snapshot.get("native_artifacts", {})
    pinned = {}
    for key, ref in snapshot.get("native_roots", {}).items():
        old = previous.get(key)
        if old and old.get("document_ref") == ref:
            pinned[key] = old
        job = find_preview(db, ctx, change, key)
        if not job or job.status != "succeeded":
            continue
        output = db.scalar(
            select(Generation)
            .where(
                Generation.job_id == job.id,
                Generation.project_id == ctx.project.id,
            )
            .order_by(Generation.created_at, Generation.id)
            .limit(1)
        )
        plan = (output.input_refs or {}).get("engineering_plan", {}) if output else {}
        if output and plan.get("root") == ref:
            pinned[key] = {
                "document_ref": ref,
                "generation_id": str(output.id),
                "blob_hash": output.output_blob_hash,
                "output_type": output.output_type,
                "executor": plan["executor"],
            }
    if pinned:
        snapshot["native_artifacts"] = pinned
    else:
        snapshot.pop("native_artifacts", None)
