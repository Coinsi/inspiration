"""Persistent task history and execution shared by providers and local media tools."""

import copy
import io
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.adapters.contracts import GenerationRequest, ReferenceImage
from app.core import audit
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import CapabilityUnsupported, Conflict, NotFound, QuotaExceeded
from app.models.generation import Generation, GenerationJob, Quota
from app.models.storage import Blob
from app.modules.generation import media_engine
from app.storage import cas

ACTIVE = ("pending", "submitted", "running")
pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="media-job")


def event(job, status, message):
    job.status = status
    raw = dict(job.cost_raw or {})
    raw["events"] = [
        *raw.get("events", []),
        {"at": datetime.now(UTC).isoformat(), "status": status, "message": message},
    ][-100:]
    job.cost_raw = raw


def validate_target(db, project_id, target_type, target_id):
    from app.models.asset import Asset
    from app.models.creation import Creation
    from app.models.shot import Shot
    from app.models.timeline import Timeline
    from app.models.work import CreativeWork

    cls = {
        "shot": Shot,
        "asset": Asset,
        "timeline": Timeline,
        "creation": Creation,
        "work": CreativeWork,
    }.get(target_type)
    target = db.get(cls, target_id) if cls else None
    if target is None or target.project_id != project_id or getattr(target, "deleted_at", None):
        raise NotFound("目标不存在")
    return target


def source(db, project_id, gen_id, image_only=False):
    g = db.get(Generation, gen_id)
    if g is None or g.project_id != project_id or not g.output_blob_hash:
        raise NotFound("素材不存在")
    if image_only and g.output_type != "image":
        raise CapabilityUnsupported("请选择图片素材")
    return g


def reserve_check(db, project_id, points):
    if points <= 0:
        return
    # Serialize submits/retries for a project, including when there is no quota row yet.
    from app.models.identity import Project

    db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    from app.modules.generation.service import get_project_quota

    quota = get_project_quota(db, project_id)
    reserved = sum(
        float(j.estimated_cost or 0)
        for j in db.scalars(
            select(GenerationJob).where(
                GenerationJob.project_id == project_id, GenerationJob.status.in_(ACTIVE)
            )
        )
    )
    if (
        quota
        and quota.limit_cost
        and float(quota.used_cost) + reserved + points > float(quota.limit_cost)
    ):
        raise QuotaExceeded("项目点数不足（包含排队任务的预留点数）")


def dispatch(db, job):
    if not (job.cost_raw or {}).get("events"):
        event(job, "pending", "已加入队列")
    job.cost_raw = {
        **(job.cost_raw or {}),
        "executor": "eager" if settings.celery_eager else settings.generation_executor,
    }
    from app.modules.engineering import delivery

    if delivery.eligible(job):
        delivery.enqueue(db, job)
        db.commit()
        if settings.celery_eager:
            execute(db, job.id)
        # Normal workers publish from the durable outbox, including after restart.
        db.refresh(job)
        return job
    db.commit()
    if settings.celery_eager:
        execute(db, job.id)
    elif settings.generation_executor == "local":
        pool.submit(_local, job.id)
    else:
        try:
            from app.tasks.generation_tasks import run_generation_job

            run_generation_job.apply_async(args=[str(job.id)], retry=False)
        except Exception:
            db.refresh(job)
            if job.status == "pending":
                job.error = "任务队列连接失败，请启动 worker / Redis，或配置本地执行器后重试"
                event(job, "failed", job.error)
                db.commit()
    db.refresh(job)
    return job


def _local(job_id):
    with SessionLocal() as db:
        execute(db, job_id)


def recover_local():
    """Single-process local executor only: surface interrupted work and resume its queue."""
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(GenerationJob).where(
                    GenerationJob.status.in_(ACTIVE),
                    GenerationJob.cost_raw["executor"].astext == "local",
                )
            )
        )
        pending = []
        for job in rows:
            from app.models.engineering import EngineeringDelivery
            from app.modules.engineering import delivery

            if delivery.eligible(job) and db.get(EngineeringDelivery, job.id):
                continue  # Durable dispatcher and expiring ownership handle this job.
            if job.status == "pending":
                pending.append(job.id)
            else:
                job.error = "本地服务重启，任务执行已中断；云端请求可能已计费，请确认后重试"
                event(job, "failed", job.error)
        db.commit()
        for job_id in pending:
            pool.submit(_local, job_id)


def local_job(db, ctx, target_type, target_id, operation, snapshot, output_type, *, defer=False):
    validate_target(db, ctx.project.id, target_type, target_id)
    job = GenerationJob(
        project_id=ctx.project.id,
        target_type=target_type,
        target_id=target_id,
        provider="local",
        request_type=output_type,
        status="pending",
        estimated_cost=0,
        created_by=ctx.user.id,
        params={},
        input_snapshot={
            "operation": operation,
            "prompt": "",
            "request_type": output_type,
            **snapshot,
        },
    )
    db.add(job)
    db.flush()
    audit.record(
        db,
        action=f"media.{operation}",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type=target_type,
        target_id=target_id,
        detail={"job": str(job.id)},
    )
    return job if defer else dispatch(db, job)


def refine(db, ctx, gen_id, options):
    g = source(db, ctx.project.id, gen_id, True)
    x, y, w, h = options.crop
    import math

    if (
        not all(math.isfinite(v) for v in options.crop)
        or min(x, y) < 0
        or min(w, h) <= 0
        or x + w > 1.00001
        or y + h > 1.00001
    ):
        raise CapabilityUnsupported("裁剪区域必须在图片内")
    return local_job(
        db,
        ctx,
        g.target_type,
        g.target_id,
        "refine",
        {
            "source_generation_id": str(g.id),
            "source_blob": g.output_blob_hash,
            "options": options.model_dump(),
        },
        "image",
    )


def timeline_render_snapshot(db, ctx, timeline_id, revision=None):
    from app.modules.timeline import editing
    from app.modules.timeline.schemas import TimelineDocumentIn

    timeline = editing.lock(db, ctx.project.id, timeline_id)
    if revision is not None and revision != timeline.revision:
        raise Conflict("时间线已修改，请重新载入后再导出")

    data = TimelineDocumentIn.model_validate(editing.document(db, ctx.project.id, timeline_id))
    return document_render_snapshot(db, ctx, timeline_id, data)[1]


def document_render_snapshot(db, ctx, timeline_id, data):
    from app.modules.timeline import editing

    data, cues, audio, visuals = editing.prepare_document(db, ctx, timeline_id, data)
    clips = []
    for item in data.items:
        shot = validate_target(db, ctx.project.id, "shot", item.shot_id) if item.shot_id else None
        gen_id = item.generation_id or (shot.selected_generation_id if shot else None)
        if not gen_id:
            raise CapabilityUnsupported("片段尚未选择素材")
        g = source(db, ctx.project.id, gen_id)
        item.generation_id = g.id
        if g.output_type not in ("image", "video"):
            raise CapabilityUnsupported("画面片段仅支持图片或视频")
        if shot and (g.target_type != "shot" or g.target_id != shot.id):
            raise CapabilityUnsupported("素材不属于该镜头")
        duration = (item.out_point_ms - item.in_point_ms) if item.out_point_ms else item.duration_ms
        if duration <= 0:
            raise CapabilityUnsupported("请为每个片段设置正数时长或有效的出点")
        clips.append(
            {
                "shot_id": str(shot.id) if shot else None,
                "generation_id": str(g.id),
                "blob_hash": g.output_blob_hash,
                "output_type": g.output_type,
                "in_point_ms": item.in_point_ms,
                "duration_ms": duration,
                "transition": item.transition,
            }
        )
    from app.modules.media.timing import duration as timeline_duration

    try:
        total = timeline_duration(clips)
    except ValueError as exc:
        raise CapabilityUnsupported(str(exc)) from exc
    if not clips or len(clips) > 100 or total > 1_800_000:
        raise CapabilityUnsupported("请选择 1–100 段素材，总时长不超过 30 分钟")
    return data.model_dump(mode="json"), {
        "clips": clips,
        "tracks": {
            "timeline_revision": data.revision,
            "subtitles": cues,
            "audio": [{**a.model_dump(mode="json"), "blob_hash": blob.hash} for a, blob in audio],
            "visuals": visuals,
        },
    }


def render_timeline(db, ctx, timeline_id, options):
    snapshot = timeline_render_snapshot(db, ctx, timeline_id, options.revision)
    return local_job(
        db,
        ctx,
        "timeline",
        timeline_id,
        "render",
        {
            "clips": snapshot["clips"],
            "options": {**options.model_dump(), **snapshot["tracks"]},
        },
        "video",
    )


def cancel(db, ctx, job_id):
    job = db.scalar(
        select(GenerationJob)
        .where(GenerationJob.id == job_id, GenerationJob.project_id == ctx.project.id)
        .with_for_update()
    )
    if not job:
        raise NotFound("任务不存在")
    if job.target_type == "timeline":
        from app.core.permissions import require

        require(ctx.role, "timeline.edit")
    if job.status not in ACTIVE:
        raise Conflict("任务已经结束")
    event(job, "canceled", "已取消本地执行/结果接收；已发送的云端请求可能仍然计费")
    audit.record(
        db,
        action="generation.cancel",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="job",
        target_id=job.id,
    )
    db.flush()
    return job


def retry(db, ctx, job_id, *, defer_dispatch=False):
    job = db.scalar(
        select(GenerationJob)
        .where(GenerationJob.id == job_id, GenerationJob.project_id == ctx.project.id)
        .with_for_update()
    )
    if not job:
        raise NotFound("任务不存在")
    if job.target_type == "timeline":
        from app.core.permissions import require

        require(ctx.role, "timeline.edit")
    if job.status not in ("failed", "canceled"):
        raise Conflict("仅失败或取消的任务可以重试")
    if job.provider.startswith("personal_") and job.created_by != ctx.user.id:
        raise Conflict("只能由原提交者重试使用个人服务的任务")
    from app.modules.generation.model_routing import retry_allowed

    if not retry_allowed(job):
        raise Conflict("上游提交状态不确定，禁止自动重发；请核对上游记录后再创建新的生成任务")
    existing = db.scalar(
        select(GenerationJob).where(
            GenerationJob.project_id == ctx.project.id,
            GenerationJob.input_snapshot["retry_of"].astext == str(job.id),
            GenerationJob.status.in_((*ACTIVE, "succeeded")),
        )
    )
    if existing:
        raise Conflict("该任务已经重试，请查看新任务")
    validate_target(db, ctx.project.id, job.target_type, job.target_id)
    reserve_check(db, ctx.project.id, float(job.estimated_cost or 0))
    snapshot = copy.deepcopy(job.input_snapshot)
    from app.modules.generation.references import validate_snapshot as validate_references

    validate_references(db, ctx.project.id, snapshot, user_id=ctx.user.id)
    from app.modules.media.generation_reference import public_base

    # A user-triggered retry is a new submission; do not keep a retired domain.
    snapshot["reference_origin"] = public_base() or snapshot.get("reference_origin", "")
    if snapshot.get("library_origin"):
        from app.modules.library.materialization import validate_snapshot

        validate_snapshot(db, ctx.project.id, snapshot)
        target = validate_target(db, ctx.project.id, job.target_type, job.target_id)
        if target.status == "locked":
            raise Conflict("镜头已锁定，请先解锁")
    snapshot["retry_of"] = str(job.id)
    routing_state = {}
    state = job.routing_state or {}
    from app.modules.generation.model_routing import resumable_profile

    if state.get("resume") or state.get("phase") in ("accepted", "generated"):
        resume = state.get("resume") or state
        route = next(
            (
                r
                for r in snapshot.get("routing", {}).get("candidates", [])
                if r["provider_name"] == resume.get("provider_name")
            ),
            None,
        )
        if snapshot.get("personal_service"):
            route = {"model_profile": snapshot.get("model_profile", {})}
        if (
            not route
            or not resumable_profile(route["model_profile"])
            or not resume.get("external_job_id")
        ):
            raise Conflict(
                "上游可能已经生成结果，此协议暂不支持恢复下载；请核对上游结果，不会重新生成"
            )
        routing_state = {
            "resume": {
                "provider_name": resume["provider_name"],
                "external_job_id": resume["external_job_id"],
            }
        }
    new = GenerationJob(
        project_id=ctx.project.id,
        target_type=job.target_type,
        target_id=job.target_id,
        provider=job.provider,
        request_type=job.request_type,
        status="pending",
        estimated_cost=job.estimated_cost,
        created_by=ctx.user.id,
        params=copy.deepcopy(job.params),
        input_snapshot=snapshot,
        routing_state=routing_state,
    )
    db.add(new)
    db.flush()
    audit.record(
        db,
        action="generation.retry",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="job",
        target_id=new.id,
        detail={"retry_of": str(job.id)},
    )
    return new if defer_dispatch else dispatch(db, new)


def _harness_output_limit(snapshot):
    """Only new application-owned production jobs opt into a frozen hard cap."""
    limits = snapshot.get("harness_resource_limits")
    if limits is None:
        return None
    value = limits.get("output_bytes") if isinstance(limits, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("制作任务的输出大小限制无效，未执行生成")
    return value


def _check_output_limit(outputs, limit):
    if limit is None:
        return
    total = sum(data.stat().st_size if isinstance(data, Path) else len(data) for data, _ in outputs)
    if total > limit:
        raise ValueError(f"制作输出总大小超过已预留上限（{limit} 字节），未保存任何结果")


def execute(db, job_id):
    # Claim exactly once; external work runs without holding a transaction open.
    job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
    if not job or job.status != "pending":
        db.rollback()
        return
    from app.modules.engineering import delivery

    lease_token = delivery.claim(db, job) if delivery.eligible(job) else None
    if lease_token:
        db.info.setdefault(delivery.LEASE_CONTEXT, {})[job_id] = lease_token
    event(job, "submitted", "正在准备输入")
    db.commit()
    stop_heartbeat = None
    snap = copy.deepcopy(job.input_snapshot)
    project_id, provider_name = job.project_id, job.provider
    db.commit()

    def canceled():
        with SessionLocal() as check:
            return check.scalar(
                select(GenerationJob.status).where(GenerationJob.id == job_id)
            ) == "canceled" or not delivery.owned(check, job_id, lease_token)

    def stop_remote(handle):
        # Losing a lease is not a user cancellation. A newer worker may already
        # be receiving this exact upstream job; never cancel it from a stale one.
        with SessionLocal() as check:
            if not delivery.owned(check, job_id, lease_token):
                raise delivery.LeaseLost("Generation worker no longer owns this receipt")
            explicit_cancel = check.scalar(
                select(GenerationJob.status).where(GenerationJob.id == job_id)
            ) == "canceled"
        if explicit_cancel:
            try:
                provider.cancel(handle)
            except Exception:
                pass
            raise media_engine.Canceled()

    files = ExitStack()
    from app.modules.media.generation_reference import reference_origin

    files.enter_context(reference_origin(snap.get("reference_origin", "")))
    cached_files = {}

    def disk_blob(key):
        if key not in cached_files:
            blob = db.get(Blob, key)
            if not blob:
                raise ValueError("源文件不存在")
            cached_files[key] = files.enter_context(cas.local_file(blob, canceled))
        return cached_files[key]

    def read_blob(key):
        blob = db.get(Blob, key)
        if not blob:
            raise ValueError("源文件不存在")
        try:
            return cas.read_bytes(blob)
        except Exception as exc:
            raise ValueError("无法读取源文件，请检查对象存储服务或重新上传素材") from exc

    try:
        output_limit = _harness_output_limit(snap)
        from app.modules.harness.job_authorization import validate_job

        submission_phase = (
            "resume" if (job.routing_state or {}).get("harness_receipt_recovery") else "submit"
        )
        validate_job(db, job, snap, phase=submission_phase)
        db.commit()
        if lease_token:
            stop_heartbeat = delivery.heartbeat(job_id, lease_token, SessionLocal)
        from app.modules.generation.service import _materialize_references, _provider_instance

        validate_target(db, project_id, job.target_type, job.target_id)
        from app.modules.generation.references import validate_snapshot as validate_references

        validate_references(db, project_id, snap, user_id=job.created_by)
        db.commit()
        operation = snap.get("operation", "generate")
        materialized = []
        materialized_mask = None
        provider = None
        if provider_name != "local":
            materialized = _materialize_references(
                db, [ReferenceImage(**r) for r in snap.get("references", [])]
            )
            provider = _provider_instance(
                db, project_id, provider_name, snap, user_id=job.created_by
            )
            if hasattr(provider, "bind_job"):
                provider.bind_job(job_id)
            if snap.get("mask"):
                materialized_mask = _materialize_references(db, [ReferenceImage(**snap["mask"])])[0]
                if "inpaint" not in provider.capabilities().features:
                    raise ValueError("供应商当前不支持局部重绘")
        db.commit()
        job = db.scalar(
            select(GenerationJob)
            .where(GenerationJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job.status == "canceled" or not delivery.owned(db, job_id, lease_token):
            db.rollback()
            return
        event(
            job,
            "running",
            {
                "refine": "正在处理图片",
                "render": "正在合成 MP4",
                "engineering_image": "正在合成工程图像",
                "engineering_render": "正在渲染工程内容",
                "video_frames": "正在提取视频帧",
                "video_audio": "正在提取音轨",
                "video_trim": "正在导出视频片段",
                "library_materialize": "正在从原视频制作创作素材",
                "character": "正在根据参考图生成角色素材",
            }.get(operation, "正在向供应商提交并等待结果"),
        )
        db.commit()
        cost_raw = {}
        warnings = []
        media_details = []
        output_mimes = []
        if operation == "refine":
            outputs = [
                (
                    media_engine.transform_image(read_blob(snap["source_blob"]), snap["options"]),
                    "image",
                )
            ]
        elif operation in ("engineering_image", "engineering_render"):
            from app.modules.engineering import executors

            outputs = [
                files.enter_context(
                    executors.execute(snap["engineering_plan"], read_blob, disk_blob, canceled)
                )
            ]
            output_mimes = [executors.resolve(snap["engineering_plan"]["executor"]).mime]
        elif operation == "render":
            outputs = [
                (
                    files.enter_context(
                        media_engine.render_file(
                            snap["clips"], disk_blob, snap["options"], canceled
                        )
                    ),
                    "video",
                )
            ]
        elif operation == "library_materialize":
            from app.modules.library.materialization import process

            data, typ, details = process(db, snap, canceled)
            outputs = [(data, typ)]
            media_details = [details]
        elif operation in ("video_frames", "video_audio", "video_trim"):
            from app.modules.generation.video_tools import process_files

            processed = files.enter_context(
                process_files(disk_blob(snap["source_blob"]), snap["options"], canceled)
            )
            outputs = [(data, typ) for data, typ, _ in processed]
            media_details = [details for _, _, details in processed]
        else:
            req = GenerationRequest(
                request_type=snap["request_type"],
                prompt=snap["prompt"],
                params=snap.get("params", {}),
                provider_params=snap.get("provider_params", {}),
                count=snap.get("count", 1),
                references=materialized,
                mask=materialized_mask,
            )
            validate_job(db, job, snap, phase=submission_phase)
            if delivery.policy(job) == "remote_receipt" and submission_phase != "resume":
                delivery.remote_phase(db, job_id, "dispatching")
            delivery.fence(db, job_id)
            db.commit()
            handle = provider.submit(req)
            if getattr(provider, "actual_route", None):
                snap["model_profile"] = provider.actual_route["model_profile"]
            # Do not overwrite a concurrent cancellation while recording the remote handle.
            job = db.scalar(
                select(GenerationJob)
                .where(GenerationJob.id == job_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            delivery.fence(db, job_id)
            if delivery.policy(job) == "remote_receipt":
                delivery.remote_phase(db, job_id, "accepted", handle.external_job_id)
            job.external_job_id = handle.external_job_id
            db.commit()
            result = None
            for _ in range(600):
                if canceled():
                    stop_remote(handle)
                try:
                    delivery.fence(db, job_id)
                    validate_job(db, job, snap, phase="receipt")
                    db.commit()
                except delivery.LeaseLost:
                    raise
                except Exception:
                    with SessionLocal() as check:
                        still_owned = delivery.owned(check, job_id, lease_token)
                    if still_owned:
                        try:
                            provider.cancel(handle)
                        except Exception:
                            pass
                    raise
                result = provider.poll(handle)
                delivery.fence(db, job_id)
                if result.status != "running":
                    break
                time.sleep(2)
            if not result or result.status != "succeeded" or not result.outputs:
                raise ValueError(
                    (result.error if result else None) or "供应商未返回有效结果或任务超时"
                )
            if delivery.policy(job) == "remote_receipt":
                delivery.remote_phase(db, job_id, "generated", handle.external_job_id)
                db.commit()
            outputs = []
            received_bytes = 0
            for out in result.outputs:
                data = out.data
                if data is None and out.url:
                    download_limit = 256 * 1024 * 1024
                    if output_limit is not None:
                        download_limit = min(download_limit, max(0, output_limit - received_bytes))
                    with urllib.request.urlopen(out.url, timeout=120) as response:
                        data = response.read(download_limit + 1)
                    if len(data) > 256 * 1024 * 1024:
                        raise ValueError("供应商输出超过 256 MB")
                if not data:
                    raise ValueError("供应商返回了空文件")
                received_bytes += len(data)
                if output_limit is not None and received_bytes > output_limit:
                    raise ValueError("供应商输出总大小超过已预留上限，未保存任何结果")
                if out.type != req.request_type:
                    raise ValueError("供应商返回的媒体类型与请求不一致")
                data, dimensions = media_engine.validate_output(data, out.type)
                media_details.append(dimensions)
                requested_size = snap.get("provider_params", {}).get("size")
                if dimensions and requested_size and "x" in str(requested_size):
                    actual_size = f"{dimensions['width']}x{dimensions['height']}"
                    if requested_size != actual_size:
                        warnings.append(
                            f"供应商未遵守请求尺寸 {requested_size}，实际返回 {actual_size}；"
                            "请核对网关支持的参数"
                        )
                outputs.append((data, out.type))
            cost_raw = result.cost_raw or {}
        # Check the complete batch before even its first CAS write. Local tools
        # return temporary paths, providers may return bytes or normalized images.
        # Partial writes would consume storage despite a failed budget decision.
        _check_output_limit(outputs, output_limit)
        if operation in {"engineering_image", "engineering_render", "render", "refine"}:
            # Persist real output metadata before downstream tools see the file.
            # A native audio preview is also a reusable production input.
            from PIL import Image

            from app.modules.generation.video_tools import probe_file

            media_details = []
            for data, typ in outputs:
                if typ == "image":
                    with Image.open(data if isinstance(data, Path) else io.BytesIO(data)) as image:
                        media_details.append({"width": image.width, "height": image.height})
                elif isinstance(data, Path):
                    media_details.append(probe_file(data, canceled))
                else:
                    directory = Path(files.enter_context(tempfile.TemporaryDirectory()))
                    target = directory / "output.media"
                    target.write_bytes(data)
                    media_details.append(probe_file(target, canceled))
        if canceled():
            raise media_engine.Canceled()
        _check_output_limit(outputs, output_limit)
        receipt_phase = "receipt" if provider_name != "local" and job.external_job_id else "submit"
        validate_job(db, job, snap, phase=receipt_phase)
        db.commit()
        # Store bytes before the final row lock so cancel is still responsive during I/O.
        blobs = [
            (
                (cas.put_file if isinstance(data, Path) else cas.put_bytes)(
                    db,
                    data,
                    output_mimes[index]
                    if index < len(output_mimes)
                    else {"video": "video/mp4", "image": "image/png", "audio": "audio/mp4"}[typ],
                    **{
                        name: media_details[index][name]
                        for name in ("width", "height", "duration_ms")
                        if index < len(media_details) and name in media_details[index]
                    },
                ),
                typ,
            )
            for index, (data, typ) in enumerate(outputs)
        ]
        job = db.scalar(
            select(GenerationJob)
            .where(GenerationJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job.status == "canceled" or not delivery.owned(db, job_id, lease_token):
            db.rollback()
            return
        validate_job(db, job, snap, phase=receipt_phase)
        points = (
            provider.actual_cost()
            if hasattr(provider, "actual_cost")
            else float(job.estimated_cost or 0)
        )
        for index, (blob, typ) in enumerate(blobs):
            db.add(
                Generation(
                    project_id=job.project_id,
                    job_id=job.id,
                    target_type=job.target_type,
                    target_id=job.target_id,
                    provider=job.provider,
                    model=snap.get("model_profile", {}).get("model"),
                    output_type=typ,
                    output_blob_hash=blob.hash,
                    prompt_snapshot=snap.get("prompt", ""),
                    params=snap.get("params", {}),
                    input_refs={
                        **(
                            {
                                "routing": {
                                    "model_id": snap["routing"]["model_id"],
                                    "model_revision": snap["routing"]["revision"],
                                    "model_name": snap["routing"]["name"],
                                    "provider_name": (job.routing_state or {}).get("provider_name"),
                                    "channel_name": (job.routing_state or {}).get("channel_name"),
                                }
                            }
                            if snap.get("routing")
                            else {}
                        ),
                        "variant_index": index,
                        "skills": snap.get("skills", []),
                        "original_prompt": snap.get("original_prompt", snap.get("prompt", "")),
                        **(
                            {"agent_origin": snap["agent_origin"]}
                            if snap.get("agent_origin")
                            else {}
                        ),
                        **(
                            {"canvas_origin": snap["canvas_origin"]}
                            if snap.get("canvas_origin")
                            else {}
                        ),
                        "operation": operation,
                        **(
                            {
                                "engineering_plan": {
                                    "executor": snap["engineering_plan"]["executor"],
                                    "root": snap["engineering_plan"]["root"],
                                    "fingerprint": snap["engineering_plan"]["fingerprint"],
                                }
                            }
                            if snap.get("engineering_plan")
                            else {}
                        ),
                        **(
                            {
                                "engineering_change_id": snap["engineering_change_id"],
                                "engineering_work_id": snap["engineering_work_id"],
                            }
                            if snap.get("engineering_change_id")
                            else {}
                        ),
                        **(
                            {"library_origin": snap["library_origin"]}
                            if snap.get("library_origin")
                            else {}
                        ),
                        "source_generation_id": snap.get("source_generation_id"),
                        "first_frame_id": snap.get("first_frame_id"),
                        "last_frame_id": snap.get("last_frame_id"),
                        "reference_sources": snap.get("reference_sources", []),
                        "character_preset": snap.get("character_preset"),
                        "clips": snap.get("clips", []),
                        "options": snap.get("options", {}),
                        "references": snap.get("references", []),
                        "mask": snap.get("mask"),
                        "edit_source_id": snap.get("edit_source_id"),
                        "provider_params": snap.get("provider_params", {}),
                        "actual_media": media_details[index] if index < len(media_details) else {},
                    },
                    cost_points=points / len(blobs),
                    cost_raw=cost_raw,
                )
            )
        event(job, "succeeded", f"已保存 {len(blobs)} 个结果")
        from app.modules.generation.personal import record_result

        record_result(db, job)
        if warnings:
            job.cost_raw = {**(job.cost_raw or {}), "warnings": warnings}
        job.actual_cost = points
        # Atomic increment prevents simultaneous completions losing usage.
        from sqlalchemy import update

        db.execute(
            update(Quota)
            .where(
                Quota.project_id == job.project_id,
                Quota.scope == "project",
                Quota.user_id.is_(None),
            )
            .values(used_cost=Quota.used_cost + points)
        )
        db.commit()
    except (media_engine.Canceled, delivery.LeaseLost):
        db.rollback()
    except Exception as exc:
        db.rollback()
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
        if job and job.status != "canceled" and delivery.owned(db, job_id, lease_token):
            job.error = str(exc)[:2000]
            event(job, "failed", job.error)
            from app.modules.generation.personal import record_result

            record_result(db, job)
            db.commit()
    finally:
        if stop_heartbeat:
            stop_heartbeat()
        files.close()
        db.info.get(delivery.LEASE_CONTEXT, {}).pop(job_id, None)
        # A stale error may have reacquired the job row before discovering that
        # its token is obsolete. Release that transaction before cleanup opens
        # another session, or its own row lock would deadlock cleanup.
        db.rollback()
        delivery.release(job_id, lease_token, SessionLocal)
