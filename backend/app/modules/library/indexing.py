"""Project-filtered, version-pinned search. Inference is delegated to our own service."""

import base64
import io
import json
import math
import urllib.request

from PIL import Image
from sqlalchemy import ARRAY, Float, bindparam, func, select, text

from app.core import audit
from app.core.config import settings
from app.core.errors import CapabilityUnsupported, Conflict, NotFound, ProviderError
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaAnnotation, MediaIndex, MediaSegment
from app.modules.library import service


def inference(path, payload=None):
    headers = {"Content-Type": "application/json"}
    if settings.library_indexer_token:
        headers["Authorization"] = f"Bearer {settings.library_indexer_token}"
    request = urllib.request.Request(
        settings.library_indexer_url.rstrip("/") + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=180 if payload else 5) as response:
            return json.loads(response.read(1_000_000))
    except Exception as exc:
        raise ProviderError("本地内容检索服务暂不可用，请检查索引服务后重试") from exc


def embed(payload, expected=None):
    result = inference("/embed", payload)
    values = result.get("embedding", [])
    if len(values) != 512 or any(
        not isinstance(v, (float, int)) or not math.isfinite(v) for v in values
    ):
        raise ProviderError("索引服务返回的向量无效")
    norm = math.sqrt(sum(v * v for v in values))
    if norm < 1e-8 or not result.get("model_key") or (expected and result["model_key"] != expected):
        raise ProviderError("索引模型已变化，请使用相同模型或重建索引")
    return result["model_key"], [v / norm for v in values]


def index_out(i):
    return {
        k: getattr(i, k)
        for k in (
            "id",
            "version_id",
            "status",
            "model_key",
            "total",
            "completed",
            "error",
            "cancel_requested",
        )
    }


def submit(db, ctx, version_id):
    _, v = service.version(db, ctx.project.id, version_id, lock=True)
    if v.status != "ready":
        raise Conflict("视频预览完成后才能建立索引")
    active = db.scalar(
        select(MediaIndex).where(
            MediaIndex.version_id == v.id, MediaIndex.status.in_(["queued", "processing"])
        )
    )
    if active:
        return index_out(active)
    health = inference("/health")
    if health.get("dimensions") != 512 or not health.get("model_key"):
        raise ProviderError("索引服务协议不兼容")
    run = db.scalar(
        select(MediaIndex)
        .where(
            MediaIndex.version_id == v.id,
            MediaIndex.status == "failed",
            MediaIndex.model_key == health["model_key"],
        )
        .order_by(MediaIndex.created_at.desc())
        .limit(1)
    )
    if run:
        run.status, run.error, run.cancel_requested = "queued", None, False
    else:
        run = MediaIndex(version_id=v.id, model_key=health["model_key"])
        db.add(run)
    db.flush()
    audit.record(
        db,
        action="library.index.submit",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=v.id,
        detail={"index_id": str(run.id), "model_key": run.model_key},
    )
    return index_out(run)


def cancel(db, ctx, index_id):
    run = db.get(MediaIndex, index_id)
    if not run:
        raise NotFound("索引任务不存在")
    service.version(db, ctx.project.id, run.version_id, lock=True)
    db.refresh(run)
    if run.status in ("queued", "processing"):
        run.cancel_requested = True
        if run.status == "queued":
            run.status = "canceled"
    return index_out(run)


def status(db, project_id):
    versions = list(
        db.scalars(
            select(MediaVersion)
            .join(LibraryMedia)
            .where(
                LibraryMedia.project_id == project_id,
                LibraryMedia.deleted_at.is_(None),
                MediaVersion.status == "ready",
            )
        )
    )
    runs = list(
        db.scalars(
            select(MediaIndex)
            .join(MediaVersion)
            .join(LibraryMedia)
            .where(LibraryMedia.project_id == project_id, LibraryMedia.deleted_at.is_(None))
            .order_by(MediaIndex.created_at.desc())
        )
    )
    latest = {}
    ready = set()
    for run in runs:
        latest.setdefault(str(run.version_id), index_out(run))
        if run.status == "ready":
            ready.add(run.version_id)
    return {
        "versions": len(versions),
        "indexed": len(ready),
        "runs": latest,
        "unindexed": sum(v.id not in ready for v in versions),
        "sampling": {"window_ms": 10000, "step_ms": 8000, "frame_step_ms": 2000},
    }


def add_annotation(db, ctx, version_id, data):
    _, v = service.version(db, ctx.project.id, version_id, lock=True)
    if v.status != "ready" or data.end_ms > v.duration_ms or data.end_ms - data.start_ms < 100:
        raise CapabilityUnsupported("标注范围必须位于视频内，且至少0.1秒")
    if not data.text.strip():
        raise CapabilityUnsupported("标注内容不能为空")
    item = MediaAnnotation(
        version_id=v.id, created_by=ctx.user.id, **{**data.model_dump(), "text": data.text.strip()}
    )
    db.add(item)
    db.flush()
    audit.record(
        db,
        action="library.annotation.create",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=item.id,
        detail={"version_id": str(v.id)},
    )
    return {"id": item.id}


def remove_annotation(db, ctx, annotation_id):
    item = db.get(MediaAnnotation, annotation_id)
    if not item:
        raise NotFound("标注不存在")
    service.version(db, ctx.project.id, item.version_id, lock=True)
    db.delete(item)
    audit.record(
        db,
        action="library.annotation.remove",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=annotation_id,
    )


def annotations(db, project_id, version_id, offset=0):
    service.version(db, project_id, version_id)
    query = select(MediaAnnotation).where(MediaAnnotation.version_id == version_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "total": total,
        "items": [
            {k: getattr(a, k) for k in ("id", "start_ms", "end_ms", "kind", "text", "source")}
            for a in db.scalars(
                query.order_by(MediaAnnotation.start_ms, MediaAnnotation.id)
                .offset(offset)
                .limit(50)
            )
        ],
    }


def import_subtitles(db, ctx, version_id, content):
    from app.modules.media.subtitles import parse_srt

    _, v = service.version(db, ctx.project.id, version_id, lock=True)
    if v.status != "ready":
        raise Conflict("视频处理完成后才能导入字幕")
    try:
        cues = parse_srt(content, v.duration_ms)
    except ValueError as exc:
        raise CapabilityUnsupported(str(exc)) from exc
    existing = {
        (a.start_ms, a.end_ms, a.text)
        for a in db.scalars(
            select(MediaAnnotation).where(
                MediaAnnotation.version_id == version_id, MediaAnnotation.kind == "speech"
            )
        )
    }
    added = 0
    for cue in cues:
        key = (cue["start_ms"], cue["end_ms"], cue["text"])
        if key in existing:
            continue
        db.add(MediaAnnotation(version_id=version_id, created_by=ctx.user.id, kind="speech", **cue))
        existing.add(key)
        added += 1
    audit.record(
        db,
        action="library.subtitles.import",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=version_id,
        detail={"added": added, "skipped": len(cues) - added},
    )
    return {"added": added, "skipped": len(cues) - added}


def image_payload(raw):
    try:
        if len(raw) > 3_000_000:
            raise ValueError()
        image = Image.open(io.BytesIO(base64.b64decode(raw, validate=True)))
        if image.width * image.height > 25_000_000:
            raise ValueError()
        image = image.convert("RGB")
        image.thumbnail((768, 768))
        out = io.BytesIO()
        image.save(out, "JPEG", quality=85)
        return base64.b64encode(out.getvalue()).decode()
    except Exception as exc:
        raise CapabilityUnsupported("请选择有效且小于2 MB的参考图片") from exc


def search(db, ctx, data):
    coverage = status(db, ctx.project.id)
    if data.mode == "annotated":
        where = [LibraryMedia.project_id == ctx.project.id, LibraryMedia.deleted_at.is_(None)]
        if data.query.strip():
            where.append(MediaAnnotation.text.icontains(data.query.strip(), autoescape=True))
        if data.kind:
            where.append(MediaAnnotation.kind == data.kind)
        query = (
            select(MediaAnnotation, MediaVersion, LibraryMedia)
            .select_from(MediaAnnotation)
            .join(MediaVersion)
            .join(LibraryMedia)
            .where(*where)
        )
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.execute(
            query.order_by(MediaAnnotation.created_at.desc(), MediaAnnotation.id)
            .offset(data.offset)
            .limit(data.limit)
        ).all()
        return {
            "coverage": coverage,
            "total": total,
            "items": [
                result(a.id, v, m, a.start_ms, a.end_ms, v.poster_hash, None, a.text, a.kind)
                for a, v, m in rows
            ],
        }
    if not data.query.strip() and not data.image:
        raise CapabilityUnsupported("请输入画面描述或选择参考图片")
    payload = {"text": data.query.strip()}
    if data.image:
        payload["images"] = [image_payload(data.image)]
    if not coverage["indexed"]:
        return {"coverage": coverage, "has_more": False, "items": []}
    key, vector = embed(payload)
    # Correlated dot product of normalized vectors, entirely within the project-filtered SQL.
    # MRL512 is retained in our own PostgreSQL; no hosted vector-index dependency.
    score = (
        text(
            "SELECT sum(p.a*p.b) FROM unnest(media_segment.embedding, "
            "CAST(:vector AS double precision[])) AS p(a,b)"
        )
        .bindparams(bindparam("vector", type_=ARRAY(Float)))
        .columns(value=Float)
        .scalar_subquery()
    )
    if settings.enable_pgvector:
        from pgvector.sqlalchemy import Vector
        from sqlalchemy import cast

        # Exact distance after project filtering. Native vector arithmetic avoids
        # expanding 512 SQL rows per segment and does not trade recall for ANN speed.
        score = 1 - cast(MediaSegment.embedding, Vector(512)).cosine_distance(vector)
    latest_ready = (
        select(MediaIndex.id)
        .join(MediaVersion)
        .join(LibraryMedia)
        .where(
            MediaIndex.status == "ready",
            MediaIndex.model_key == key,
            LibraryMedia.project_id == ctx.project.id,
            LibraryMedia.deleted_at.is_(None),
        )
        .distinct(MediaIndex.version_id)
        .order_by(MediaIndex.version_id, MediaIndex.created_at.desc(), MediaIndex.id)
    )
    compatible = db.scalar(select(func.count()).select_from(latest_ready.subquery()))
    coverage = {
        **coverage,
        "indexed": compatible,
        "unindexed": coverage["versions"] - compatible,
        "incompatible": coverage["indexed"] - compatible,
    }
    query = (
        select(MediaSegment, MediaVersion, LibraryMedia, score)
        .select_from(MediaSegment)
        .join(MediaIndex)
        .join(MediaVersion)
        .join(LibraryMedia)
        .where(
            LibraryMedia.project_id == ctx.project.id,
            LibraryMedia.deleted_at.is_(None),
            MediaIndex.id.in_(latest_ready),
        )
    )
    if data.collapse_versions:
        ranked = (
            select(
                MediaSegment.id,
                func.row_number()
                .over(
                    partition_by=MediaIndex.version_id,
                    order_by=(score.desc(), MediaSegment.id),
                )
                .label("position"),
            )
            .join(MediaIndex)
            .where(MediaIndex.id.in_(latest_ready))
            .subquery()
        )
        query = query.where(MediaSegment.id.in_(select(ranked.c.id).where(ranked.c.position == 1)))
    rows = db.execute(
        query.order_by(score.desc(), MediaSegment.id).offset(data.offset).limit(data.limit + 1),
        {"vector": vector},
    ).all()
    return {
        "coverage": coverage,
        "model_key": key,
        "has_more": len(rows) > data.limit,
        "items": [
            result(
                s.id,
                v,
                m,
                s.start_ms,
                s.end_ms,
                s.frames[0]["hash"] if s.frames else v.poster_hash,
                float(similarity),
                "",
                "sampled_video",
            )
            for s, v, m, similarity in rows[: data.limit]
        ],
    }


def result(id, v, m, start, end, thumbnail, score, description, kind):
    return {
        "id": id,
        "media_id": m.id,
        "version_id": v.id,
        "ordinal": v.ordinal,
        "name": m.name,
        "start_ms": start,
        "end_ms": end,
        "thumbnail_hash": thumbnail,
        "proxy_hash": v.proxy_hash,
        "score": score,
        "description": description,
        "kind": kind,
    }
