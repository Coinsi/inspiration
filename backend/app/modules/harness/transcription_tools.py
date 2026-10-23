"""Bridge the existing checkpointed ASR queue, without pretending it is a video job."""

import hashlib
import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import CapabilityUnsupported, Conflict, Forbidden
from app.core.permissions import require
from app.models.harness import ProductionRun, ProductionTask
from app.models.transcription import TranscriptionRun
from app.modules.harness.context import ContextReference, revalidate_context
from app.modules.harness.execution_guard import authorize
from app.modules.harness.media_context import describe_media
from app.modules.harness.resources import reserve_units
from app.modules.library import service as library
from app.modules.tools import capabilities, registry
from app.modules.tools.capabilities import ToolPolicy
from app.modules.transcription import schemas, service


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TranscribeArgs(Strict):
    version_id: uuid.UUID
    expected_blob_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    language: Literal["zh", "en", "ja", "ko", "fr", "de", "es"] | None = None


class StatusArgs(Strict):
    transcription_id: uuid.UUID
    revision: int | None = Field(default=None, ge=0, strict=True)
    offset: int = Field(default=0, ge=0, le=10000, strict=True)
    limit: int = Field(default=100, ge=1, le=200, strict=True)


MAX_DURATION_MS = 1_800_000
MAX_SOURCE_BYTES = 256 * 1024 * 1024


def availability():
    reason = None
    if not settings.transcriber_url:
        reason = "尚未配置转写服务"
    elif settings.harness_transcription_cost_mode != "self_hosted":
        reason = (
            "转写服务计费策略未知；需要管理员确认自托管服务并配置 "
            "HARNESS_TRANSCRIPTION_COST_MODE=self_hosted"
        )
    elif not settings.transcription_worker_enabled:
        reason = "转写工作进程未启用"
    return {
        "available": reason is None,
        "configured": bool(settings.transcriber_url),
        "cost_mode": settings.harness_transcription_cost_mode,
        "reason": reason,
        "health_checked": False,
        "limits": {"duration_ms": MAX_DURATION_MS, "source_bytes": MAX_SOURCE_BYTES},
        "word_alignment": False,
        "automatic_publish": False,
    }


def _fingerprint(args):
    return hashlib.sha256(
        json.dumps(args.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()


def preflight(db, ctx, context, args):
    ctx = revalidate_context(db, ctx)
    require(ctx.role, "asset.edit")
    check = availability()
    if not check["available"]:
        raise CapabilityUnsupported(check["reason"])
    info = describe_media(
        db,
        ctx,
        ContextReference(
            kind="media_version", id=args.version_id, expected_blob_hash=args.expected_blob_hash
        ),
    )
    if info["media_type"] not in {"video", "audio"}:
        raise CapabilityUnsupported("转写仅接受固定音视频素材")
    length = info["metadata"].get("duration_ms")
    if not length or length > MAX_DURATION_MS or info["size_bytes"] > MAX_SOURCE_BYTES:
        raise CapabilityUnsupported("转写源须有已确认时长、最多30分钟及256MB")
    if info["media_type"] == "video" and info["metadata"].get("has_audio") is False:
        raise CapabilityUnsupported("视频不包含可转写音轨")
    return {
        "reference": info["reference"],
        "duration_ms": length,
        "source_bytes": info["size_bytes"],
        "cost_mode": "self_hosted",
        "model_charge_points": 0,
        "word_alignment": False,
    }


def observe(db, ctx, transcription_id, *, revision=None, offset=0, limit=100):
    ctx = revalidate_context(db, ctx)
    row = service.get(db, ctx, transcription_id)
    if revision is not None and row.revision != revision:
        raise Conflict("字幕修订已变化，请读取当前版本后再选择")
    data = service.out(row, False)
    cues, size = [], 0
    for cue in row.cues[offset : offset + limit]:
        size += len(json.dumps(cue, ensure_ascii=False))
        if size > 16000:
            break
        cues.append(cue)
    return {
        **data,
        "id": str(row.id),
        "version_id": str(row.version_id),
        "transcription_id": str(row.id),
        "async_kind": "transcription",
        "cues": cues,
        "offset": offset,
        "next_offset": offset + len(cues) if offset + len(cues) < len(row.cues) else None,
        "draft_ready": row.status == "ready",
        "adopted": False,
        "coverage": {
            "sentence_segments": len(row.cues),
            "completed_chunks": row.completed,
            "total_chunks": row.total,
            "word_alignment": False,
        },
        "unverified": ["recognition_accuracy", "speaker_identity", "word_timing"],
        "requires_review": True,
    }


def status(db, ctx, context, args):
    return observe(
        db, ctx, args.transcription_id, revision=args.revision, offset=args.offset, limit=args.limit
    )


def transcribe(db, ctx, context, args):
    ctx = revalidate_context(db, ctx)
    require(ctx.role, "asset.edit")
    authorize(db, ctx, context)
    grant = context.input_context.get("execution", {})
    try:
        run_id, task_id, key = (
            uuid.UUID(str(grant[k])) for k in ("run_id", "task_id", "request_key")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Forbidden("转写需要系统持久化的任务及请求标识") from exc
    run = db.get(ProductionRun, run_id)
    task = db.get(ProductionTask, task_id)
    if (
        not run
        or not task
        or task.run_id != run.id
        or "candidate" not in run.policy.get("effects", [])
    ):
        raise Forbidden("此任务没有创建转写草稿的权限")
    ledger = dict(run.context.get("transcription_reservations", {}))
    fingerprint = _fingerprint(args)
    prior = ledger.get(str(key))
    if prior:
        if prior["task_id"] != str(task.id) or prior["fingerprint"] != fingerprint:
            raise Conflict("此系统请求标识已用于不同转写输入")
        # A lost receipt never submits recognition again, even if the service
        # is down, the config changed, or a completed draft was subsequently edited.
        return {**observe(db, ctx, uuid.UUID(prior["transcription_id"])), "reused": True}
    info = preflight(db, ctx, context, args)
    library.version(db, ctx.project.id, args.version_id, lock=True)
    active = db.scalar(
        select(TranscriptionRun).where(
            TranscriptionRun.version_id == args.version_id,
            TranscriptionRun.status.in_(["queued", "processing"]),
        )
    )
    if active:
        raise Conflict(
            "该素材已有转写任务，请读取现有任务；不能另起并行转写",
            {"transcription_id": str(active.id)},
        )
    with db.begin_nested():
        transfer = info["source_bytes"] + int(info["duration_ms"] * 32 * 4 / 3) + 4096
        reserve_units(
            run,
            "transcription:" + str(key),
            {"output_storage_bytes": 1_000_000, "media_transfer_bytes": transfer},
        )
        started = service.start(
            db,
            ctx,
            schemas.Create(version_id=args.version_id, language=args.language, new_run=True),
            defer_health=True,
        )
        ledger[str(key)] = {
            "task_id": str(task.id),
            "transcription_id": str(started["id"]),
            "fingerprint": fingerprint,
            "source": info["reference"],
            "cost_mode": "self_hosted",
            "duration_ms": info["duration_ms"],
            "model_charge_points": 0,
        }
        run.context = {**run.context, "transcription_reservations": ledger}
        db.flush()
    # Intent, resource reservation and request identity are durable before the
    # independent queue observes them. No network call occurs under these locks.
    db.commit()
    return {**observe(db, ctx, started["id"]), "reused": False, "model_charge_points": 0}


def register_tools():
    definitions = [
        (
            "media.transcribe",
            "从固定音视频素材启动自托管转写草稿，30秒分段保存，可续跑；返回 transcription_id。"
            "管理员未确认自托管计费策略时不可用，不自动发布字幕，不生成配音，不提供逐字时间。",
            TranscribeArgs,
            transcribe,
            ToolPolicy(
                effect="candidate",
                permissions=("asset.edit",),
                idempotency="business_key",
                cancellation="job_cooperative",
                recovery="query_job",
                starts_job=True,
                output_kind="transcription_job",
                output_schema={
                    "type": "object",
                    "required": ["transcription_id", "status", "draft_ready"],
                },
            ),
        ),
        (
            "media.transcription_status",
            "读取转写状态和有界分页字幕草稿；可固定 revision 防止读取期间发生编辑。",
            StatusArgs,
            status,
            ToolPolicy(
                effect="read",
                idempotency="read_only",
                cancellation="not_applicable",
                recovery="repeat_read",
                output_kind="transcription_draft",
            ),
        ),
    ]
    for name, description, schema, handler, policy in definitions:
        if name not in registry.CATALOG:
            registry.register(registry.Tool(name, description, schema, group="media"), handler)
            capabilities.register_contract(name, policy)
    from app.modules.harness.plan_contracts import _PREFLIGHTS, register_preflight

    if "media.transcribe" not in _PREFLIGHTS:
        register_preflight("media.transcribe", preflight)
