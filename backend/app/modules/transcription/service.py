import json
import math
import urllib.request

from sqlalchemy import delete, select, update

from app.core import audit
from app.core.config import settings
from app.core.errors import CapabilityUnsupported, Conflict, NotFound, ProviderError
from app.models.media_index import MediaAnnotation
from app.models.transcription import TranscriptionRun
from app.modules.library import service as library


def inference(path, payload=None):
    if not settings.transcriber_url:
        raise ProviderError("尚未配置语音识别服务")
    headers = {"Content-Type": "application/json"}
    if settings.transcriber_token:
        headers["Authorization"] = "Bearer " + settings.transcriber_token
    req = urllib.request.Request(
        settings.transcriber_url.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload else None,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=120 if payload else 5) as response:
            return json.loads(response.read(1_000_000))
    except Exception as exc:
        raise ProviderError("语音识别服务暂不可用，请检查服务后重试") from exc


def get(db, ctx, id, lock=False):
    r = db.scalar(
        select(TranscriptionRun).where(
            TranscriptionRun.id == id, TranscriptionRun.project_id == ctx.project.id
        )
    )
    if not r:
        raise NotFound("转写任务不存在")
    library.version(db, ctx.project.id, r.version_id, lock=lock)
    if lock:
        db.refresh(r, with_for_update=True)
    return r


def out(r, detail=True):
    data = {
        k: getattr(r, k)
        for k in (
            "id",
            "version_id",
            "status",
            "model_key",
            "language",
            "total",
            "completed",
            "revision",
            "published_revision",
            "cancel_requested",
            "error",
            "created_at",
        )
    }
    data["cue_count"] = len(r.cues)
    if detail:
        data["cues"] = r.cues
    return data


def start(db, ctx, data):
    _, v = library.version(db, ctx.project.id, data.version_id, lock=True)
    if v.status != "ready":
        raise Conflict("视频预览完成后才能转写")
    r = db.scalar(
        select(TranscriptionRun).where(
            TranscriptionRun.version_id == v.id,
            TranscriptionRun.status.in_(["queued", "processing"]),
        )
    )
    if r:
        return out(r)
    health = inference("/health")
    key = health.get("model_key")
    if not key or len(key) > 255:
        raise ProviderError("语音识别服务协议不兼容")
    if not data.new_run:
        old = db.scalar(
            select(TranscriptionRun)
            .where(
                TranscriptionRun.version_id == v.id,
                TranscriptionRun.model_key == key,
                TranscriptionRun.language == data.language,
                TranscriptionRun.status == "ready",
            )
            .order_by(TranscriptionRun.created_at.desc())
            .limit(1)
        )
        if old:
            return out(old)
    r = TranscriptionRun(
        project_id=ctx.project.id,
        version_id=v.id,
        model_key=key,
        language=data.language,
        total=math.ceil(v.duration_ms / 30000),
        created_by=ctx.user.id,
    )
    db.add(r)
    db.flush()
    audit.record(
        db,
        action="transcription.start",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=r.id,
        detail={"version_id": str(v.id), "model_key": key},
    )
    return out(r)


def save(db, ctx, id, data):
    r = get(db, ctx, id, True)
    if r.status != "ready":
        raise Conflict("转写完成后才能修改字幕")
    if r.revision != data.revision:
        raise Conflict("字幕草稿已有新版本，请重新载入")
    _, v = library.version(db, ctx.project.id, r.version_id)
    if any(c.end_ms > v.duration_ms for c in data.cues):
        raise CapabilityUnsupported("字幕超出原片时长")
    r.cues = sorted(
        [c.model_dump(mode="json") for c in data.cues],
        key=lambda c: (c["start_ms"], c["end_ms"], c["id"]),
    )
    r.revision += 1
    audit.record(
        db,
        action="transcription.edit",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=r.id,
        detail={"revision": r.revision, "count": len(r.cues)},
    )
    return out(r)


def publish(db, ctx, id, revision):
    r = get(db, ctx, id, True)
    if r.status != "ready" or r.revision != revision:
        raise Conflict("请先保存最新字幕，再发布到检索")
    if r.published_revision == revision:
        return {"published": len(r.cues), "revision": revision}
    # One reviewed ASR publication per source version; handwritten/SRT entries survive.
    db.execute(
        delete(MediaAnnotation).where(
            MediaAnnotation.version_id == r.version_id,
            MediaAnnotation.source["transcription_id"].astext.is_not(None),
        )
    )
    db.execute(
        update(TranscriptionRun)
        .where(TranscriptionRun.version_id == r.version_id, TranscriptionRun.id != r.id)
        .values(published_revision=None)
    )
    for cue in r.cues:
        db.add(
            MediaAnnotation(
                version_id=r.version_id,
                start_ms=cue["start_ms"],
                end_ms=cue["end_ms"],
                kind="speech",
                text=cue["text"],
                created_by=ctx.user.id,
                source={
                    "transcription_id": str(r.id),
                    "model_key": r.model_key,
                    "revision": revision,
                    "cue_id": cue["id"],
                    "reviewed": True,
                },
            )
        )
    r.published_revision = revision
    audit.record(
        db,
        action="transcription.publish",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=r.id,
        detail={"revision": revision, "count": len(r.cues)},
    )
    return {"published": len(r.cues), "revision": revision}
