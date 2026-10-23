"""Reference-aware search over the same retrieval services used by the library UI."""

from typing import Literal

from pydantic import Field

from app.core.errors import CapabilityUnsupported
from app.modules.harness.context import ContextReference, resolve_reference, revalidate_context
from app.modules.harness.schemas import Strict
from app.modules.library import indexing, schemas
from app.modules.tools import capabilities, registry
from app.modules.tools.capabilities import ToolPolicy


class Search(Strict):
    query: str = Field(default="", max_length=2000)
    mode: Literal["semantic", "annotated"] = "semantic"
    engine: Literal["local", "wise"] = "local"
    target: Literal["visual", "audio", "face", "object"] = "visual"
    kind: Literal["visual", "speech"] | None = None
    reference: ContextReference | None = None
    frame_time_ms: int | None = Field(default=None, ge=0)
    bbox: schemas.SearchBBox | None = None
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=12, ge=1, le=30)
    collapse_versions: bool = True


def search(db, ctx, context, args):
    ctx = revalidate_context(db, ctx)
    payload = args.model_dump(exclude={"reference", "frame_time_ms"})
    if args.reference:
        from app.modules.harness.media_context import inspect_media

        if args.mode != "semantic":
            raise CapabilityUnsupported("标注检索使用文字条件，参考媒体请选语义检索")
        observed = inspect_media(
            db,
            ctx,
            args.reference,
            frame_times_ms=[args.frame_time_ms] if args.frame_time_ms is not None else [],
            include_audio=args.target == "audio",
            include_transcript=False,
        )
        parts = observed.get("content_parts", [])
        if args.target == "audio":
            audio = next(
                (p.get("input_audio") for p in parts if p.get("type") == "input_audio"), None
            )
            if not audio:
                raise CapabilityUnsupported("参考素材没有可用试听片段，请指定音频及有效范围")
            payload["audio"] = "data:audio/wav;base64," + audio["data"]
        else:
            image = next((p.get("image_url") for p in parts if p.get("type") == "image_url"), None)
            if not image:
                raise CapabilityUnsupported("参考素材没有可用画面，请指定图片或视频取帧位置")
            payload["image"] = image["url"]
    elif args.frame_time_ms is not None:
        raise CapabilityUnsupported("取帧时间需要固定参考素材")
    data = schemas.MediaSearchIn.model_validate(payload)
    if data.engine == "wise" and data.mode == "semantic":
        from app.modules.library.wise_search import search as wise_search

        result = wise_search(db, ctx, data)
    else:
        result = indexing.search(db, ctx, data)
    # Search hits are suggestions, not proof that the content meets the creative brief.
    for item in result.get("items", []):
        identity = item.get("version_id")
        if identity:
            ref = {
                "kind": "media_version",
                "id": str(identity),
                "purpose": "检索命中，使用前检查内容",
            }
            if (
                item.get("media_type") != "image"
                and item.get("start_ms") is not None
                and item.get("end_ms") is not None
            ):
                ref.update(start_ms=item["start_ms"], end_ms=item["end_ms"])
            item["reference"] = resolve_reference(db, ctx, ref)["reference"]
    return {**result, "engine": data.engine, "mode": data.mode, "content_verified": False}


def register_tools():
    name = "media.search"
    if name in registry.CATALOG:
        return
    tool = registry.Tool(
        name=name,
        description="检索已有素材的画面、标注、台词、声音、人物或物体，支持固定图片/视频取帧/音频参考。"
        "与素材库共享实际检索服务；未部署的可选引擎会明确返回不可用。命中不代表已看过素材。",
        schema=Search,
        group="media",
    )
    registry.register(tool, search)
    capabilities.register_contract(
        name,
        ToolPolicy(
            effect="read",
            idempotency="read_only",
            cancellation="not_applicable",
            recovery="repeat_read",
            output_kind="fixed_media_search_results",
        ),
    )
