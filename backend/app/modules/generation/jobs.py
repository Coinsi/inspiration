"""Persistent task history and execution shared by providers and local media tools."""

import copy
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

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
        {"at": datetime.now(timezone.utc).isoformat(), "status": status, "message": message},
    ][-100:]
    job.cost_raw = raw


def validate_target(db, project_id, target_type, target_id):
    from app.models.asset import Asset
    from app.models.shot import Shot
    from app.models.timeline import Timeline

    cls = {"shot": Shot, "asset": Asset, "timeline": Timeline}.get(target_type)
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
            if job.status == "pending":
                pending.append(job.id)
            else:
                job.error = "本地服务重启，任务执行已中断；云端请求可能已计费，请确认后重试"
                event(job, "failed", job.error)
        db.commit()
        for job_id in pending:
            pool.submit(_local, job_id)


def local_job(db, ctx, target_type, target_id, operation, snapshot, output_type):
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
    return dispatch(db, job)


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


def render_timeline(db, ctx, timeline_id, options):
    from app.modules.timeline.service import list_items

    items = list_items(db, ctx.project.id, timeline_id)
    clips = []
    for item in items:
        shot = validate_target(db, ctx.project.id, "shot", item.shot_id)
        gen_id = item.generation_id or shot.selected_generation_id
        if not gen_id:
            raise CapabilityUnsupported(f"镜头 {shot.code} 尚未选择素材")
        g = source(db, ctx.project.id, gen_id)
        if g.target_type != "shot" or g.target_id != shot.id:
            raise CapabilityUnsupported("素材不属于该镜头")
        duration = (item.out_point_ms - item.in_point_ms) if item.out_point_ms else item.duration_ms
        if duration <= 0:
            raise CapabilityUnsupported("请为每个片段设置正数时长或有效的出点")
        if item.transition:
            raise CapabilityUnsupported("当前导出支持直切，请清除转场设置后导出")
        clips.append(
            {
                "shot_id": str(shot.id),
                "generation_id": str(g.id),
                "blob_hash": g.output_blob_hash,
                "output_type": g.output_type,
                "in_point_ms": item.in_point_ms,
                "duration_ms": duration,
            }
        )
    if not clips or len(clips) > 100 or sum(c["duration_ms"] for c in clips) > 1_800_000:
        raise CapabilityUnsupported("请选择 1–100 段素材，总时长不超过 30 分钟")
    return local_job(
        db,
        ctx,
        "timeline",
        timeline_id,
        "render",
        {"clips": clips, "options": options.model_dump()},
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


def retry(db, ctx, job_id):
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
    snapshot["retry_of"] = str(job.id)
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
    return dispatch(db, new)


def execute(db, job_id):
    # Claim exactly once; external work runs without holding a transaction open.
    job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
    if not job or job.status != "pending":
        db.rollback()
        return
    event(job, "submitted", "正在准备输入")
    db.commit()
    snap = copy.deepcopy(job.input_snapshot)
    project_id, provider_name = job.project_id, job.provider
    db.commit()

    def canceled():
        with SessionLocal() as check:
            return (
                check.scalar(select(GenerationJob.status).where(GenerationJob.id == job_id))
                == "canceled"
            )

    def read_blob(key):
        blob = db.get(Blob, key)
        if not blob:
            raise ValueError("源文件不存在")
        try:
            return cas.read_bytes(blob)
        except Exception as exc:
            raise ValueError("无法读取源文件，请检查对象存储服务或重新上传素材") from exc

    try:
        from app.modules.generation.service import _materialize_references, _provider_instance

        validate_target(db, project_id, job.target_type, job.target_id)
        db.commit()
        operation = snap.get("operation", "generate")
        materialized = []
        provider = None
        if provider_name != "local":
            materialized = _materialize_references(
                db, [ReferenceImage(**r) for r in snap.get("references", [])]
            )
            provider = _provider_instance(db, project_id, provider_name)
        db.commit()
        job = db.scalar(
            select(GenerationJob)
            .where(GenerationJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job.status == "canceled":
            db.rollback()
            return
        event(
            job,
            "running",
            {"refine": "正在处理图片", "render": "正在合成 MP4"}.get(
                operation, "正在向供应商提交并等待结果"
            ),
        )
        db.commit()
        cost_raw = {}
        warnings = []
        media_details = []
        if operation == "refine":
            outputs = [
                (
                    media_engine.transform_image(read_blob(snap["source_blob"]), snap["options"]),
                    "image",
                )
            ]
        elif operation == "render":
            outputs = [
                (media_engine.render(snap["clips"], read_blob, snap["options"], canceled), "video")
            ]
        else:
            req = GenerationRequest(
                request_type=snap["request_type"],
                prompt=snap["prompt"],
                params=snap.get("params", {}),
                provider_params=snap.get("provider_params", {}),
                count=snap.get("count", 1),
                references=materialized,
            )
            handle = provider.submit(req)
            # Do not overwrite a concurrent cancellation while recording the remote handle.
            db.refresh(job)
            job.external_job_id = handle.external_job_id
            db.commit()
            result = None
            for _ in range(600):
                if canceled():
                    try:
                        provider.cancel(handle)
                    except Exception:
                        pass
                    raise media_engine.Canceled()
                result = provider.poll(handle)
                if result.status != "running":
                    break
                time.sleep(2)
            if not result or result.status != "succeeded" or not result.outputs:
                raise ValueError(
                    (result.error if result else None) or "供应商未返回有效结果或任务超时"
                )
            outputs = []
            for out in result.outputs:
                data = out.data
                if data is None and out.url:
                    with urllib.request.urlopen(out.url, timeout=120) as response:
                        data = response.read(256 * 1024 * 1024 + 1)
                    if len(data) > 256 * 1024 * 1024:
                        raise ValueError("供应商输出超过 256 MB")
                if not data:
                    raise ValueError("供应商返回了空文件")
                if out.type != req.request_type:
                    raise ValueError("供应商返回的媒体类型与请求不一致")
                data, dimensions = media_engine.validate_output(data, out.type)
                media_details.append(dimensions)
                requested_size = snap.get("provider_params", {}).get("size")
                if dimensions and requested_size and requested_size != "auto":
                    actual_size = f"{dimensions['width']}x{dimensions['height']}"
                    if requested_size != actual_size:
                        warnings.append(
                            f"供应商未遵守请求尺寸 {requested_size}，实际返回 {actual_size}；请核对网关支持的参数"
                        )
                outputs.append((data, out.type))
            cost_raw = result.cost_raw or {}
        if canceled():
            raise media_engine.Canceled()
        # Store bytes before the final row lock so cancel is still responsive during I/O.
        blobs = [
            (cas.put_bytes(db, data, "video/mp4" if typ == "video" else "image/png"), typ)
            for data, typ in outputs
        ]
        job = db.scalar(
            select(GenerationJob)
            .where(GenerationJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job.status == "canceled":
            db.rollback()
            return
        points = float(job.estimated_cost or 0)
        for index, (blob, typ) in enumerate(blobs):
            db.add(
                Generation(
                    project_id=job.project_id,
                    job_id=job.id,
                    target_type=job.target_type,
                    target_id=job.target_id,
                    provider=job.provider,
                    output_type=typ,
                    output_blob_hash=blob.hash,
                    prompt_snapshot=snap.get("prompt", ""),
                    params=snap.get("params", {}),
                    input_refs={
                        "operation": operation,
                        "source_generation_id": snap.get("source_generation_id"),
                        "clips": snap.get("clips", []),
                        "options": snap.get("options", {}),
                        "references": snap.get("references", []),
                        "provider_params": snap.get("provider_params", {}),
                        "actual_media": media_details[index] if index < len(media_details) else {},
                    },
                    cost_points=points / len(blobs),
                    cost_raw=cost_raw,
                )
            )
        event(job, "succeeded", f"已保存 {len(blobs)} 个结果")
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
    except media_engine.Canceled:
        db.rollback()
    except Exception as exc:
        db.rollback()
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
        if job and job.status != "canceled":
            job.error = str(exc)[:2000]
            event(job, "failed", job.error)
            db.commit()
