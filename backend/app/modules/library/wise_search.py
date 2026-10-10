"""Translate search hits back to authorized, immutable library versions."""

import base64
import hashlib
import io
import json
import math
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy import and_, or_, select

from app.core.config import settings
from app.core.errors import CapabilityUnsupported, ProviderError
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_processing import MediaProcessingRun
from app.modules.library import indexing
from app.modules.library.processing import MEDIA_CAPABILITIES, enabled_capabilities
from app.modules.library.providers.wise import WiseClient
from app.modules.library.schemas import SearchBBox


def visible_versions(db, project_id, target):
    """Refresh both tombstones and the selected target's accepted run identity."""
    eligible_types = [
        kind for kind, capabilities in MEDIA_CAPABILITIES["wise"].items() if target in capabilities
    ]
    eligible = MediaVersion.media_type.in_(eligible_types)
    if target == "audio":
        # Unknown legacy stream metadata remains eligible. A confirmed silent
        # video must not count as missing audio indexing or authorize old hits.
        eligible = and_(
            eligible,
            or_(MediaVersion.media_type != "video", MediaVersion.has_audio.is_not(False)),
        )
    rows = db.execute(
        select(MediaProcessingRun, MediaVersion, LibraryMedia)
        .select_from(MediaVersion)
        .join(LibraryMedia, LibraryMedia.id == MediaVersion.media_id)
        .outerjoin(
            MediaProcessingRun,
            and_(
                MediaProcessingRun.version_id == MediaVersion.id,
                MediaProcessingRun.provider == "wise",
            ),
        )
        .where(
            LibraryMedia.project_id == project_id,
            LibraryMedia.deleted_at.is_(None),
            MediaVersion.status == "ready",
            eligible,
        )
        .order_by(MediaProcessingRun.created_at.desc(), MediaProcessingRun.id.desc())
        .execution_options(populate_existing=True)
    ).all()
    # Include versions without runs in this same SQL snapshot. A separate count
    # could race another trash and yield a negative or stale coverage value.
    total = len({version.id for _, version, _ in rows})
    versions, identities = {}, {}
    invalidated, selected = set(), set()
    for run, version, media in rows:
        if run is None:
            continue
        if run.operation == "delete":
            invalidated.add(str(version.id))
            continue
        if (
            run.status != "ready"
            or target not in run.capabilities
            or str(version.id) in invalidated | selected
        ):
            continue
        selected.add(str(version.id))
        if run.outputs.get("capabilities", {}).get(target, {}).get("state") == "ready":
            versions.setdefault(str(version.id), (version, media))
            identities[str(version.id)] = run.id
    return (
        versions,
        identities,
        {
            "provider": "wise",
            "indexed": len(versions),
            "target": target,
            "runs": {},
            "unindexed": total - len(versions),
            "versions": total,
        },
    )


def search(db, ctx, data):
    if not settings.library_processing_enabled or not settings.library_wise_url:
        raise CapabilityUnsupported("多模态检索服务尚未启用")
    if data.target not in enabled_capabilities("wise"):
        raise CapabilityUnsupported("此类检索尚未启用，请先完成对应模型的部署与验证")
    from app.modules.library.optional_capabilities import require_optional

    require_optional([data.target])
    audio = getattr(data, "audio", None)
    if sum(bool(value) for value in (data.image, audio, data.query.strip())) > 1:
        raise CapabilityUnsupported("请单独使用参考图、参考音频或文字描述进行检索")
    if audio and data.target != "audio":
        raise CapabilityUnsupported("参考音频仅用于声音检索")
    if data.image and data.target == "audio":
        raise CapabilityUnsupported("声音检索请使用参考音频或文字描述")
    versions, accepted_runs, coverage = visible_versions(db, ctx.project.id, data.target)
    if not versions or data.offset >= 100:
        return {"items": [], "coverage": coverage, "has_more": False}
    client = WiseClient(settings.library_wise_url, settings.library_wise_token)
    # One bounded candidate set; pagination and per-video grouping use the same set.
    limit = 100
    if audio:
        result = client.search_media(
            ctx.project.id,
            reference_audio(audio),
            modality="audio",
            source_ids=list(versions),
            limit=limit,
            mime_type="audio/wav",
        )
    elif data.image:
        try:
            raw = base64.b64decode(data.image.split(",", 1)[-1], validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 20_000_000:
                    raise ValueError("Query image has too many pixels")
                image = ImageOps.exif_transpose(image)
                image.thumbnail((1024, 1024))
                buf = io.BytesIO()
                image.convert("RGB").save(buf, format="JPEG")
        except Exception as exc:
            raise CapabilityUnsupported("参考图无法读取") from exc
        result = client.search_media(
            ctx.project.id,
            buf.getvalue(),
            modality=data.target,
            source_ids=list(versions),
            limit=limit,
            **({"bbox": data.bbox.model_dump()} if data.bbox else {}),
        )
    else:
        if not data.query.strip():
            raise CapabilityUnsupported("请输入搜索描述或添加参考素材")
        if data.target == "face":
            raise CapabilityUnsupported("人物检索需要提供人脸参考图")
        result = client.search(
            ctx.project.id,
            {
                "query": data.query,
                "modality": data.target,
                "source_ids": list(versions),
                "limit": limit,
            },
        )
    hits = result.get("items")
    if not isinstance(hits, list):
        raise ProviderError("检索服务返回格式无效")
    # The outbox may not yet have delivered a concurrent local trash to WISE.
    # Refresh after the remote wait; a restore/new ready run must not authorize
    # an old in-flight result from a previous processing generation either.
    current, current_runs, coverage = visible_versions(db, ctx.project.id, data.target)
    versions = {
        key: value for key, value in current.items() if current_runs[key] == accepted_runs.get(key)
    }
    items, seen, grouped = [], set(), {}
    regional = data.target in ("face", "object")
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        source_id = str(hit.get("source_id", ""))
        if source_id not in versions:
            continue
        version, media = versions[source_id]
        box, vector_id = None, None
        if regional:
            # A region without its original vector identity cannot safely represent
            # multiple objects in one frame. Reject malformed provider results.
            try:
                box = SearchBBox.model_validate(hit.get("bbox")).model_dump()
                raw_id = hit.get("vector_id")
                if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)):
                    continue
                vector_id = str(raw_id)
                if not vector_id.isascii() or not vector_id.isdecimal() or len(vector_id) > 20:
                    continue
                if hit.get("modality") != data.target:
                    continue
            except ValueError:
                continue
        try:
            score = float(hit["score"])
            if not math.isfinite(score):
                continue
            if version.media_type == "image":
                start = end = None
                frame_match = False
            else:
                start_s = float(hit["start_seconds"])
                end_s = float(hit["end_seconds"])
                if not all(math.isfinite(x) for x in (start_s, end_s)):
                    continue
                start = max(0, round(start_s * 1000))
                end = min(version.duration_ms, round(end_s * 1000))
                frame_match = version.media_type == "video" and start_s == end_s
                if frame_match:
                    # Frame locations are previews, not inferred shot boundaries.
                    end = min(version.duration_ms, start + 500)
        except (KeyError, ValueError, TypeError):
            continue
        if start is not None and end <= start:
            continue
        identity = (source_id, data.target, start, end, vector_id, box)
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        key = digest if regional else (source_id, start, end)
        if key in seen:
            continue
        seen.add(key)
        item = indexing.result(
            f"wise:{source_id}:{digest}",
            version,
            media,
            start,
            end,
            version.poster_hash,
            score,
            "",
            data.target,
        )
        item["provider"] = "wise"
        item["media_type"] = version.media_type
        item["match_type"] = (
            "image" if version.media_type == "image" else "frame" if frame_match else "interval"
        )
        item["matched_at_ms"] = start if frame_match else None
        if regional:
            item["bbox"], item["vector_id"] = box, vector_id
            match = {
                name: item[name]
                for name in (
                    "id",
                    "bbox",
                    "vector_id",
                    "start_ms",
                    "end_ms",
                    "matched_at_ms",
                    "score",
                )
            }
            if data.collapse_versions and source_id in grouped:
                grouped[source_id]["matches"].append(match)
                continue
            item["matches"] = [match]
        elif data.collapse_versions and source_id in grouped:
            continue
        grouped[source_id] = item
        items.append(item)
    return {
        "items": items[data.offset : data.offset + data.limit],
        "coverage": coverage,
        "has_more": len(items) > data.offset + data.limit,
        "candidate_limit": 100,
    }


def reference_audio(value):
    """Decode a short bounded audio reference; no CAS writes or model downloads."""
    from app.modules.generation import media_engine
    from app.modules.library.media_ingest import input_options, probe_media

    try:
        raw = base64.b64decode(value.split(",", 1)[-1], validate=True)
        if not raw or len(raw) > 4 * 1024 * 1024:
            raise ValueError("Invalid reference size")
        with tempfile.TemporaryDirectory(prefix="wise-query-") as folder:
            source, output = Path(folder) / "reference", Path(folder) / "reference.wav"
            source.write_bytes(raw)
            info = probe_media(source, "audio", max_duration_ms=30_000)
            media_engine.run(
                [
                    "-xerror",
                    "-err_detect",
                    "explode",
                    *input_options(info),
                    "-i",
                    str(source),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-t",
                    "30",
                    "-ac",
                    "1",
                    "-ar",
                    "48000",
                    "-c:a",
                    "pcm_s16le",
                    str(output),
                ],
                timeout=45,
            )
            if output.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Decoded reference exceeds limit")
            return output.read_bytes()
    except Exception as exc:
        raise CapabilityUnsupported("参考音频无法读取，请选择 4 MB、30 秒以内的音频文件") from exc
