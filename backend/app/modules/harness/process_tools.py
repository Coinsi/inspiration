"""Bounded local media processing with durable, immutable candidate outputs.

Semantic inpainting, new speech/music and generative extension are deliberately
not aliases for these deterministic operations. The capability inventory exposes
that distinction so a planner cannot silently substitute a weaker operation.
"""

import hashlib
import json
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.core.errors import CapabilityUnsupported, Conflict, Forbidden
from app.core.permissions import require
from app.models.generation import GenerationJob
from app.models.harness import ProductionRun, ProductionTask
from app.models.work import CreativeWork
from app.modules.generation import jobs, schemas, video_tools
from app.modules.harness.context import ContextReference, revalidate_context
from app.modules.harness.execution_guard import authorize
from app.modules.harness.media_context import describe_media
from app.modules.harness.media_tools import observe_job
from app.modules.harness.resources import reserve_units
from app.modules.tools import capabilities, registry
from app.modules.tools.capabilities import ToolPolicy

MAX_BYTES = 256 * 1024 * 1024


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class FixedSource(Strict):
    kind: Literal["generation", "media_version"]
    id: uuid.UUID
    expected_blob_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ImageAdjust(Strict):
    mode: Literal["image.adjust"]
    crop: tuple[float, float, float, float] = (0, 0, 1, 1)
    rotate: Literal[0, 90, 180, 270] = 0
    scale: float = Field(default=1, ge=0.25, le=4)
    brightness: float = Field(default=1, ge=0.1, le=2)
    contrast: float = Field(default=1, ge=0.1, le=2)

    @model_validator(mode="after")
    def bounds(self):
        x, y, w, h = self.crop
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1 or y + h > 1:
            raise ValueError("裁剪区域必须在图片内")
        return self


Milliseconds = Annotated[int, Field(ge=0, le=1_800_000, strict=True)]


class VideoFrames(Strict):
    mode: Literal["video.frames"]
    times_ms: list[Milliseconds] = Field(min_length=1, max_length=12)


class VideoRange(Strict):
    mode: Literal["video.trim", "video.extract_audio"]
    start_ms: Milliseconds = 0
    end_ms: Annotated[int, Field(ge=100, le=1_800_000, strict=True)]


class ProcessArgs(Strict):
    work_id: uuid.UUID
    source: FixedSource
    operation: Annotated[ImageAdjust | VideoFrames | VideoRange, Field(discriminator="mode")]


class CapabilitiesArgs(Strict):
    pass


def inventory(db, ctx, context, args):
    revalidate_context(db, ctx)
    from app.modules.harness.transcription_tools import availability

    transcription = availability()
    return {
        "local_processing": {
            "tool": "media.process",
            "available": True,
            "model_charge_points": 0,
            "operations": ["image.adjust", "video.frames", "video.trim", "video.extract_audio"],
            "limits": {
                "source_bytes": MAX_BYTES,
                "source_duration_ms": 1_800_000,
                "frames": 12,
                "image_pixels": 40_000_000,
                "image_side": 8192,
            },
            "output": "unselected_immutable_generation",
            "automatic_adoption": False,
        },
        "editable_composition": {
            "tool": "engineering.compose",
            "available": "engineering.compose" in registry.CATALOG,
            "operations": [
                "image_layers_and_opacity_masks",
                "audio_trim_gain_mute",
                "video_sequence",
                "audio_fades",
                "timed_subtitles",
                "visual_overlays",
            ],
            "limitations": [
                "透明度遮罩仅控制合成，不会生成被遮住的内容",
                "字幕时间由输入提供，不是自动逐字对齐",
            ],
        },
        "limitations": [
            {
                "capability": "image.semantic_inpaint",
                "harness_available": False,
                "domain_available": True,
                "reason": (
                    "现有蒙版重绘接口只接受资产或镜头图片及声明 inpaint 的已配置模型；"
                    "尚未接入本工具的预算和稳定请求契约"
                ),
            },
            {
                "capability": "video.generative_extend",
                "harness_available": False,
                "domain_available": False,
                "reason": "本地裁剪不会生成新帧；没有已接通的延长执行器",
            },
            {
                "capability": "audio.speech_generation",
                "harness_available": False,
                "domain_available": False,
                "reason": "尚无接通的配音生成供应商",
            },
            {
                "capability": "audio.music_generation",
                "harness_available": False,
                "domain_available": False,
                "reason": "尚无接通的音乐生成供应商",
            },
            {
                "capability": "audio.sound_effect_generation",
                "harness_available": False,
                "domain_available": False,
                "reason": "尚无接通的音效生成供应商",
            },
            {
                "capability": "transcription",
                "harness_available": transcription["available"]
                and "media.transcribe" in registry.CATALOG,
                "domain_available": True,
                **transcription,
                "tool": "media.transcribe",
                "status_tool": "media.transcription_status",
            },
            {
                "capability": "subtitle.word_alignment",
                "harness_available": False,
                "domain_available": False,
                "reason": "现有字幕可编辑句段时间；没有强制逐字对齐执行器",
            },
        ],
    }


def _execution(db, ctx, context, args):
    grant = context.input_context.get("execution", {})
    try:
        key = uuid.UUID(str(grant["request_key"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise Forbidden("媒体处理需要系统提供的稳定请求标识") from exc
    if str(args.work_id) != str(context.work_id):
        raise Forbidden("处理结果必须属于当前关联作品")
    authorize(db, ctx, context)
    run, task = None, None
    if grant.get("run_id"):
        run = db.get(ProductionRun, uuid.UUID(str(grant["run_id"])))
        task = db.get(ProductionTask, uuid.UUID(str(grant["task_id"])))
        if str(run.work_id) != str(args.work_id) or "candidate" not in run.policy.get(
            "effects", []
        ):
            raise Forbidden("当前任务没有该作品的候选处理权限")
    # Also serialize non-agent tool clients. This protects the same stable key
    # without a dependency on an AgentRun or its model implementation.
    work = db.scalar(select(CreativeWork).where(CreativeWork.id == args.work_id).with_for_update())
    if not work or work.project_id != ctx.project.id:
        raise Forbidden("作品不属于当前项目")
    jobs.validate_target(db, ctx.project.id, "work", work.id)
    return key, run, task


def _recipe(args, info):
    operation = args.operation
    if info["size_bytes"] > MAX_BYTES:
        raise CapabilityUnsupported("源素材超过 256 MB 处理上限")
    metadata = info["metadata"]
    if operation.mode == "image.adjust":
        if info["media_type"] != "image":
            raise CapabilityUnsupported("图片调整需要固定图片素材")
        options = schemas.RefineIn(**operation.model_dump(exclude={"mode"})).model_dump(mode="json")
        w, h = metadata.get("width"), metadata.get("height")
        if w and h:
            x, y, cw, ch = operation.crop
            width = round((round((x + cw) * w) - round(x * w)) * operation.scale)
            height = round((round((y + ch) * h) - round(y * h)) * operation.scale)
            if min(width, height) < 1 or max(width, height) > 8192 or width * height > 40_000_000:
                raise CapabilityUnsupported("输出尺寸必须为 1–8192 像素且不超过 4000 万像素")
            cap = min(MAX_BYTES, width * height * 4 + 1024 * 1024)
        else:
            cap = MAX_BYTES
        return "refine", "image", options, cap
    if info["media_type"] != "video":
        raise CapabilityUnsupported("视频处理需要固定视频素材")
    name = {"video.frames": "frames", "video.trim": "trim", "video.extract_audio": "audio"}[
        operation.mode
    ]
    options = schemas.VideoToolIn(
        operation=name, **operation.model_dump(exclude={"mode"})
    ).model_dump(mode="json")
    # Do not guess stream presence from absent metadata; the real worker probes
    # before writing output. Known bounds are rejected before a job is queued.
    video_tools.validate_options(options)
    duration = metadata.get("duration_ms")
    if duration and (
        duration > 1_800_000
        or (name == "frames" and max(options["times_ms"]) >= duration)
        or (name != "frames" and options["end_ms"] > duration)
    ):
        raise CapabilityUnsupported("处理范围超出原视频时长或 30 分钟上限")
    if name == "audio" and metadata.get("has_audio") is False:
        raise CapabilityUnsupported("该视频没有音轨")
    count = len(options["times_ms"]) if name == "frames" else 1
    pixels = (metadata.get("width") or 8192) * (metadata.get("height") or 8192)
    cap = min(MAX_BYTES, pixels * 4 + 1024 * 1024) * count if name == "frames" else MAX_BYTES
    return (
        "video_" + name,
        {"frames": "image", "trim": "video", "audio": "audio"}[name],
        options,
        cap,
    )


def preflight(db, ctx, context, args):
    ctx = revalidate_context(db, ctx)
    require(ctx.role, "generation.trigger")
    require(ctx.role, "engineering.edit")
    if str(args.work_id) != str(context.work_id):
        raise Forbidden("处理结果必须属于当前关联作品")
    jobs.validate_target(db, ctx.project.id, "work", args.work_id)
    info = describe_media(db, ctx, ContextReference(**args.source.model_dump()))
    operation, output, options, cap = _recipe(args, info)
    return {
        "reference": info["reference"],
        "operation": operation,
        "output_type": output,
        "model_charge_points": 0,
        "reserved_output_bytes": cap,
    }


def process(db, ctx, context, args):
    ctx = revalidate_context(db, ctx)
    require(ctx.role, "generation.trigger")
    require(ctx.role, "engineering.edit")
    key, run, task = _execution(db, ctx, context, args)
    info = describe_media(db, ctx, ContextReference(**args.source.model_dump()))
    operation, output, options, cap = _recipe(args, info)
    payload = args.model_dump(mode="json")
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    origin = {
        "tool": "media.process",
        "request_key": str(key),
        "fingerprint": fingerprint,
        "run_id": str(run.id) if run else None,
        "task_id": str(task.id) if task else None,
        "plan_revision": task.revision if task else None,
    }
    previous = db.scalar(
        select(GenerationJob).where(
            GenerationJob.project_id == ctx.project.id,
            GenerationJob.created_by == ctx.user.id,
            GenerationJob.input_snapshot["harness_origin"]["request_key"].astext == str(key),
        )
    )
    if previous:
        if previous.input_snapshot.get("harness_origin") != origin:
            raise Conflict("系统请求标识已用于其他处理参数或执行任务")
        return {**observe_job(db, ctx, previous), "operation": args.operation.mode, "reused": True}
    with db.begin_nested():
        job = jobs.local_job(
            db,
            ctx,
            "work",
            args.work_id,
            operation,
            {
                "source_blob": args.source.expected_blob_hash,
                "source_reference": args.source.model_dump(mode="json"),
                "options": options,
                "harness_origin": origin,
            },
            output,
            defer=True,
        )
        if run:
            reserve_units(
                run,
                "process:" + str(key),
                {"output_storage_bytes": cap, "media_transfer_bytes": cap},
                job_id=job.id,
            )
            job.input_snapshot = {
                **job.input_snapshot,
                "harness_resource_limits": {
                    "output_bytes": cap,
                    "reservation_key": "process:" + str(key),
                },
            }
        db.flush()
    jobs.dispatch(db, job)
    return {
        **observe_job(db, ctx, job),
        "operation": args.operation.mode,
        "reused": False,
        "source": args.source.model_dump(mode="json"),
        "model_charge_points": 0,
        "limitations": ["本地确定性处理，不生成缺失画面或声音；产物需审阅后采用"],
    }


def register_tools():
    definitions = [
        (
            "media.process",
            "对固定素材创建本地处理候选：图片裁剪/旋转/缩放/明暗对比、视频抽帧/裁剪/提取音轨。"
            "不调用收费模型，不改变原文件；返回 job_id 后等待技术校验，不自动采用。",
            ProcessArgs,
            process,
            ToolPolicy(
                effect="candidate",
                permissions=("generation.trigger", "engineering.edit"),
                scope="work",
                idempotency="business_key",
                cancellation="job_cooperative",
                recovery="query_job",
                starts_job=True,
                output_kind="local_media_processing_job",
                output_schema={"type": "object", "required": ["job_id", "status", "materialized"]},
            ),
        ),
        (
            "media.capabilities",
            "查看当前真实媒体处理和可编辑制作能力、配置要求与未接通能力；不能把裁剪当成视频延长，不能把合成遮罩当成语义修图。",
            CapabilitiesArgs,
            inventory,
            ToolPolicy(
                effect="read",
                idempotency="read_only",
                cancellation="not_applicable",
                recovery="repeat_read",
                output_kind="media_capability_inventory",
            ),
        ),
    ]
    for name, description, schema, handler, policy in definitions:
        if name not in registry.CATALOG:
            registry.register(registry.Tool(name, description, schema, group="media"), handler)
            capabilities.register_contract(name, policy)

    from app.modules.harness.plan_contracts import _PREFLIGHTS, register_preflight

    if "media.process" not in _PREFLIGHTS:
        register_preflight("media.process", preflight)
