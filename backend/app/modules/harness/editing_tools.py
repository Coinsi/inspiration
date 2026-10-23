"""High-level editable recipes backed by the existing engineering writers.

The tool creates one reviewed candidate. It never adopts, renders implicitly,
or calls a paid model. All recipe inputs pin immutable media and every operation
has a system-owned request key. Renderer limitations are deliberate schema
boundaries: native images have integer placement/masks, native audio has gain
and trim, and the timeline adapter provides fades and resized visual overlays.
"""

import uuid
from datetime import UTC, datetime
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.core.errors import CapabilityUnsupported, Conflict, Forbidden
from app.core.permissions import require
from app.models.engineering import EngineeringDocument
from app.models.harness import ProductionRun, ProductionTask
from app.models.storage import Blob
from app.models.timeline import Timeline
from app.modules.engineering import documents, executors, schemas, service
from app.modules.engineering.timing import encoded
from app.modules.engineering.tools import scoped
from app.modules.harness.context import ContextReference, revalidate_context
from app.modules.harness.media_context import describe_media
from app.modules.tools import capabilities, registry
from app.modules.tools.capabilities import ToolPolicy


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Milliseconds = Annotated[int, Field(ge=0, le=1_800_000, strict=True)]
Duration = Annotated[int, Field(ge=100, le=1_800_000, strict=True)]


class Source(Strict):
    kind: Literal["generation", "media_version"]
    id: uuid.UUID
    expected_blob_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class Instance(Strict):
    id: uuid.UUID | None = None
    name: str = Field(default="素材", min_length=1, max_length=255)
    source: Source


class ImageLayer(Instance):
    x: int = Field(default=0, ge=-32768, le=32768, strict=True)
    y: int = Field(default=0, ge=-32768, le=32768, strict=True)
    opacity: float = Field(default=1, ge=0, le=1)
    mask: Source | None = None
    enabled: bool = True


class ImageRecipe(Strict):
    mode: Literal["image"]
    width: int = Field(ge=1, le=4096, strict=True)
    height: int = Field(ge=1, le=4096, strict=True)
    opaque: bool = False
    layers: list[ImageLayer] = Field(min_length=1, max_length=32)


class AudioClip(Instance):
    start_ms: Milliseconds = 0
    source_start_ms: Milliseconds = 0
    duration_ms: Duration
    gain_db: float = Field(default=0, ge=-60, le=12)
    muted: bool = False


class AudioTrack(Strict):
    id: uuid.UUID | None = None
    name: str = Field(default="音轨", min_length=1, max_length=255)
    gain_db: float = Field(default=0, ge=-60, le=12)
    muted: bool = False
    clips: list[AudioClip] = Field(min_length=1, max_length=64)


class AudioRecipe(Strict):
    mode: Literal["audio"]
    channels: Literal[1, 2] = 2
    tracks: list[AudioTrack] = Field(min_length=1, max_length=24)


class Transition(Strict):
    type: Literal["dissolve", "fadeblack", "wipeleft"]
    duration_ms: int = Field(ge=100, le=2000, strict=True)


class SequenceClip(Instance):
    source_start_ms: Milliseconds = 0
    duration_ms: Duration
    transition: Transition | None = None


class SequenceAudio(AudioClip):
    kind: Literal["dialogue", "music", "effect"] = "music"
    fade_in_ms: int = Field(default=0, ge=0, le=60000, strict=True)
    fade_out_ms: int = Field(default=0, ge=0, le=60000, strict=True)


class Overlay(SequenceClip):
    start_ms: Milliseconds = 0
    track: int = Field(default=1, ge=1, le=3, strict=True)
    x: float = Field(default=0.6, ge=0, le=1)
    y: float = Field(default=0.6, ge=0, le=1)
    width: float = Field(default=0.35, ge=0.05, le=1)
    height: float = Field(default=0.35, ge=0.05, le=1)
    opacity: float = Field(default=1, ge=0, le=1)
    fit: Literal["contain", "cover"] = "contain"

    @model_validator(mode="after")
    def no_transition(self):
        if self.transition:
            raise ValueError("叠加画面不支持序列转场")
        return self


class Subtitle(Strict):
    id: uuid.UUID | None = None
    start_ms: Milliseconds
    end_ms: Milliseconds
    text: str = Field(min_length=1, max_length=4000)


class SequenceRecipe(Strict):
    mode: Literal["sequence"]
    # Omit to allocate a new empty editing shell; no selected content is changed.
    timeline_id: uuid.UUID | None = None
    timeline_revision: int = Field(default=0, ge=0, strict=True)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    clips: list[SequenceClip] = Field(min_length=1, max_length=64)
    audio: list[SequenceAudio] = Field(default_factory=list, max_length=24)
    overlays: list[Overlay] = Field(default_factory=list, max_length=12)
    subtitles: list[Subtitle] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def new_timeline(self):
        if self.timeline_id is None and self.timeline_revision:
            raise ValueError("新剪辑的时间线基线必须为零")
        return self


class ComposeArgs(Strict):
    work_id: uuid.UUID
    base_revision: int = Field(ge=0, strict=True)
    title: str = Field(min_length=1, max_length=120)
    document_id: uuid.UUID | None = None
    document_revision: int = Field(default=0, ge=0, strict=True)
    recipe: Annotated[ImageRecipe | AudioRecipe | SequenceRecipe, Field(discriminator="mode")]

    @model_validator(mode="after")
    def target(self):
        if not self.title.strip():
            raise ValueError("请填写输出名称")
        if self.document_id is None and self.document_revision:
            raise ValueError("新文档的基线必须为零")
        if self.recipe.mode == "sequence" and (self.document_id or self.document_revision):
            raise ValueError("剪辑使用 timeline_id 与 timeline_revision 定位")
        return self


def _execution(db, ctx, context):
    grant = context.input_context.get("execution", {})
    try:
        key = uuid.UUID(str(grant["request_key"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise Forbidden("制作候选需要系统注入的稳定请求标识") from exc
    provenance = schemas.Provenance()
    if grant.get("run_id") or grant.get("task_id"):
        try:
            run_id, task_id = (uuid.UUID(str(grant[k])) for k in ("run_id", "task_id"))
        except (KeyError, TypeError, ValueError) as exc:
            raise Forbidden("制作任务上下文不完整") from exc
        run = db.scalar(
            select(ProductionRun)
            .where(ProductionRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        task = db.get(ProductionTask, task_id, populate_existing=True)
        if (
            not run
            or run.project_id != ctx.project.id
            or run.created_by != ctx.user.id
            or not task
            or task.run_id != run.id
            or str(run.work_id) != str(context.work_id)
            or "candidate" not in run.policy.get("effects", [])
        ):
            raise Forbidden("当前制作任务没有此作品的候选修改权限")
        if (
            run.status not in {"running", "queued"}
            or run.context.get("guidance_pending")
            or task.status != "running"
            or str(task.lease_token) != str(grant.get("lease_token"))
            or not task.lease_expires_at
            or task.lease_expires_at <= datetime.now(UTC)
        ):
            raise Conflict("制作任务已暂停或执行租约已过期")
        provenance = schemas.Provenance(run_id=run.id, task_id=task.id, attempt_id=key)
    return key, provenance


def _resolve(db, ctx, source, allowed, *, start=0, duration=None):
    info = describe_media(db, ctx, ContextReference(**source.model_dump()))
    kind = info["media_type"]
    if kind not in allowed:
        raise CapabilityUnsupported("此编辑位置不支持该素材类型")
    if kind == "image":
        if start:
            raise CapabilityUnsupported("静态图片的源起点必须为零")
    elif duration is not None:
        length = info["metadata"].get("duration_ms")
        if length is None:
            raise CapabilityUnsupported("源素材缺少时长，请先完成媒体入库检查")
        if start + duration > length:
            raise CapabilityUnsupported("编辑范围超出固定源素材时长")
    blob = db.get(Blob, source.expected_blob_hash)
    from app.storage import cas

    try:
        with cas.open_range(blob, 0, 1) as stream:
            if not stream.read(1):
                raise ValueError("empty source")
    except (OSError, ValueError) as exc:
        raise CapabilityUnsupported("固定源素材文件不可读取") from exc
    return info


def _native(db, ctx, args, key, base):
    recipe = args.recipe
    root_id = args.document_id or uuid.uuid5(key, "output")
    commands, links, sources = [], {}, {}
    existing = {r["usage_id"]: r for r in base["resources"]}

    def source(ref, allowed, **bounds):
        info = _resolve(db, ctx, ref, allowed, **bounds)
        identity = (ref.kind, str(ref.id), ref.expected_blob_hash)
        if identity in sources:
            return sources[identity]
        origin = "library" if ref.kind == "media_version" else "generation"
        usage = uuid.uuid5(args.work_id, f"compose:{origin}:{ref.id}")
        if str(usage) not in existing:
            commands.append(
                schemas.Add(
                    operation="resource.add",
                    usage_id=usage,
                    source_kind=origin,
                    source_id=ref.id,
                    name=info["name"],
                )
            )
        else:
            previous = existing[str(usage)]
            if (
                previous["source_id"] != str(ref.id)
                or previous["blob_hash"] != ref.expected_blob_hash
            ):
                raise Conflict("作品资源使用与固定素材不一致")
        # The engineering snapshot writer uses this stable leaf identity and pins
        # the immutable source. The leaf is materialized before closure validation.
        resource_id = uuid.uuid5(args.work_id, f"resource:{origin}:{ref.id}")
        alias = f"source{len(links)}"
        links[alias] = {"target": {"id": str(resource_id), "revision": 1}, "accepts": "resource"}
        sources[identity] = alias
        return alias

    def identity(item, label):
        return str(item.id or uuid.uuid5(key, label))

    if isinstance(recipe, ImageRecipe):
        layers = []
        for i, layer in enumerate(recipe.layers):
            layers.append(
                {
                    "id": identity(layer, f"layer:{i}"),
                    "name": layer.name,
                    "source": source(layer.source, {"image"}),
                    "mask": source(layer.mask, {"image"}) if layer.mask else None,
                    "enabled": layer.enabled,
                    "transform": {"x": layer.x, "y": layer.y, "opacity": layer.opacity},
                }
            )
        payload = {
            "canvas": {
                "width": recipe.width,
                "height": recipe.height,
                "alpha": "opaque" if recipe.opaque else "straight",
            },
            "layers": layers,
        }
        kind, version = "image.document", 1
    else:
        if sum(len(t.clips) for t in recipe.tracks) > 64:
            raise CapabilityUnsupported("当前音频执行器最多支持 64 个片段")
        tracks = []
        for ti, track in enumerate(recipe.tracks):
            clips = []
            for ci, clip in enumerate(track.clips):
                cid = identity(clip, f"track:{ti}:clip:{ci}")
                length, start = (
                    Fraction(clip.duration_ms, 1000),
                    Fraction(clip.source_start_ms, 1000),
                )
                clips.append(
                    {
                        "id": cid,
                        "source": source(
                            clip.source,
                            {"audio", "video"},
                            start=clip.source_start_ms,
                            duration=clip.duration_ms,
                        ),
                        "start": encoded(Fraction(clip.start_ms, 1000)),
                        "gain_db": clip.gain_db,
                        "muted": clip.muted,
                        "source_map": {
                            "duration": encoded(length),
                            "source_domain": {
                                "start": encoded(start),
                                "end": encoded(start + length),
                            },
                            "audio_policy": "resample",
                            "segments": [
                                {
                                    "id": str(uuid.uuid5(uuid.UUID(cid), "trim")),
                                    "local": {"start": encoded(0), "end": encoded(length)},
                                    "source_start": encoded(start),
                                    "rate": encoded(1),
                                    "hold": False,
                                }
                            ],
                        },
                    }
                )
            tracks.append(
                {
                    "id": identity(track, f"track:{ti}"),
                    "name": track.name,
                    "gain_db": track.gain_db,
                    "muted": track.muted,
                    "clips": clips,
                }
            )
        payload = {"sample_rate": 48000, "channels": recipe.channels, "tracks": tracks}
        kind, version = "audio.document", 2
    commands.append(
        schemas.PutNativeDocument(
            operation="document.put",
            document_id=root_id,
            expected_revision=args.document_revision,
            kind=kind,
            schema_version=version,
            title=args.title,
            inputs=links,
            payload=payload,
        )
    )
    return commands, str(root_id), None


def _sequence(db, ctx, args, key):
    from app.modules.conversation import works
    from app.modules.timeline import library_sources
    from app.modules.timeline.schemas import TimelineDocumentIn

    require(ctx.role, "timeline.edit")
    recipe = args.recipe
    if recipe.timeline_id:
        if recipe.timeline_id not in works.timeline_ids(db, args.work_id):
            raise Forbidden("请先将目标剪辑关联到当前作品")
        tid = recipe.timeline_id
    else:
        tid = uuid.uuid5(key, "timeline")
        existing = db.get(Timeline, tid)
        if existing:
            if existing.project_id != ctx.project.id or existing.created_by != ctx.user.id:
                raise Conflict("剪辑身份不属于当前任务")
        else:
            db.add(
                Timeline(
                    id=tid,
                    project_id=ctx.project.id,
                    name=args.title,
                    kind="sequence",
                    created_by=ctx.user.id,
                )
            )
            db.flush()

    def source(item, allowed):
        _resolve(
            db, ctx, item.source, allowed, start=item.source_start_ms, duration=item.duration_ms
        )
        if item.source.kind == "generation":
            return item.source.id
        linked = library_sources.attach(db, ctx, tid, item.source.id)
        from app.models.generation import Generation

        result = db.get(Generation, uuid.UUID(linked["id"]))
        if result.output_blob_hash != item.source.expected_blob_hash:
            raise Conflict("剪辑素材连接与固定版本不一致")
        return result.id

    def identity(item, label):
        return item.id or uuid.uuid5(key, label)

    items = [
        {
            "id": identity(c, f"clip:{i}"),
            "generation_id": source(c, {"image", "video"}),
            "in_point_ms": c.source_start_ms,
            "duration_ms": c.duration_ms,
            "transition": c.transition.model_dump() if c.transition else None,
            "note": c.name,
        }
        for i, c in enumerate(recipe.clips)
    ]
    audio = [
        {
            "id": identity(c, f"audio:{i}"),
            "generation_id": source(c, {"audio"}),
            "name": c.name,
            "kind": c.kind,
            "start_ms": c.start_ms,
            "in_point_ms": c.source_start_ms,
            "duration_ms": c.duration_ms,
            "gain_db": c.gain_db,
            "muted": c.muted,
            "fade_in_ms": c.fade_in_ms,
            "fade_out_ms": c.fade_out_ms,
        }
        for i, c in enumerate(recipe.audio)
    ]
    overlays = [
        {
            "id": identity(c, f"overlay:{i}"),
            "generation_id": source(c, {"image", "video"}),
            "name": c.name,
            "track": c.track,
            "start_ms": c.start_ms,
            "in_point_ms": c.source_start_ms,
            "duration_ms": c.duration_ms,
            "x": c.x,
            "y": c.y,
            "width": c.width,
            "height": c.height,
            "opacity": c.opacity,
            "fit": c.fit,
        }
        for i, c in enumerate(recipe.overlays)
    ]
    subtitles = [
        {**c.model_dump(), "id": identity(c, f"subtitle:{i}")}
        for i, c in enumerate(recipe.subtitles)
    ]
    document = TimelineDocumentIn(
        revision=recipe.timeline_revision,
        items=items,
        audio=audio,
        visuals=overlays,
        subtitles=subtitles,
    )
    return (
        [
            schemas.EditTimeline(
                operation="timeline.edit",
                timeline_id=tid,
                document=document,
                aspect_ratio=recipe.aspect_ratio,
            )
        ],
        None,
        tid,
    )


def compose(db, ctx, context, args):
    from app.modules.harness.read_tracking import merge

    ctx = revalidate_context(db, ctx)
    require(ctx.role, "engineering.edit")
    scoped(context, args.work_id)
    key, provenance = _execution(db, ctx, context)
    # A failed recipe must not leave an empty editing shell or partial documents.
    with db.begin_nested():
        service.work(db, ctx, args.work_id, lock=True)
        base = service.snapshot_at(db, args.work_id, args.base_revision)
        if isinstance(args.recipe, SequenceRecipe):
            commands, output_id, timeline_id = _sequence(db, ctx, args, key)
        else:
            commands, output_id, timeline_id = _native(db, ctx, args, key, base)
        change = service.propose(
            db,
            ctx,
            args.work_id,
            schemas.Propose(
                request_key=key,
                base_revision=args.base_revision,
                commands=commands,
                provenance=provenance,
                read_preconditions=merge(context),
            ),
        )
        if output_id:
            ref = change["snapshot"]["native_roots"][output_id]
            plan, media_type = executors.compile_plan(db, args.work_id, ref)
            executor = plan["executor"]
        else:
            ref = change["timeline_update"]["document_ref"]
            documents.compile_sequence(db, args.work_id, ref)
            media_type, executor = "video", "timeline.ffmpeg"
        doc = db.get(EngineeringDocument, uuid.UUID(ref["id"]))
        return {
            "change_id": str(change["id"]),
            "work_id": str(args.work_id),
            "base_revision": change["base_revision"],
            "fingerprint": change["fingerprint"],
            "status": change["status"],
            "adopted": False,
            "requires_review": True,
            "output_id": output_id,
            "document_ref": ref,
            "document_kind": doc.kind,
            "timeline_id": str(timeline_id) if timeline_id else None,
            "media_type": media_type,
            "executor": executor,
            "preview_arguments": {
                "work_id": str(args.work_id),
                "change_id": str(change["id"]),
                "fingerprint": change["fingerprint"],
                "output_id": output_id,
            },
            "preview_tool": "engineering.preview",
            "rendered": False,
            "unverified": ["rendered_media", "creative_quality"],
        }


def register_tools():
    if "engineering.compose" in registry.CATALOG:
        return
    registry.register(
        registry.Tool(
            "engineering.compose",
            "用固定素材制作可编辑候选：image 整数位置/透明度/亮度遮罩；audio 裁剪/放置/音量/混音；"
            "sequence 图片视频拼接、音轨淡入淡出、缩放叠加和字幕。recipe完整替换目标输出，"
            "保留原实例时传入原id。不支持的特效不能传入。系统注入请求标识；返回预览参数后调用"
            "engineering.preview，候选不会自动采用。",
            ComposeArgs,
            group="engineering",
        ),
        compose,
    )
    capabilities.register_contract(
        "engineering.compose",
        ToolPolicy(
            effect="candidate",
            permissions=("engineering.edit",),
            scope="work",
            idempotency="business_key",
            cancellation="not_applicable",
            recovery="same_request",
            output_kind="editable_engineering_candidate",
            output_schema={
                "type": "object",
                "required": [
                    "change_id",
                    "fingerprint",
                    "document_ref",
                    "preview_arguments",
                    "adopted",
                ],
            },
        ),
    )
