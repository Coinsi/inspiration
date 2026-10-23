"""Fixed feedback locations and selective reference changes on editable documents.

Time lookup never falls through to a newer timeline. Selective editing proposes
only selected source/mask changes; it neither adopts a candidate nor pretends
that arbitrary historical parameters can be automatically inverted.
"""

import uuid
from fractions import Fraction
from typing import Literal

from pydantic import Field, model_validator

from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.core.permissions import require
from app.modules.engineering import documents, instances, native_v2, schemas, service
from app.modules.engineering.native import Span, Time
from app.modules.engineering.timing import TimeMap, affected_ranges, encoded, inverse, value
from app.modules.engineering.tools import scoped
from app.modules.harness.context import revalidate_context
from app.modules.tools import capabilities, registry
from app.modules.tools.capabilities import ToolPolicy


class FeedbackArgs(schemas.Strict):
    work_id: uuid.UUID
    document_id: uuid.UUID
    revision: int = Field(ge=1)
    instance_id: uuid.UUID | None = None
    instance_path: list[uuid.UUID] = Field(default_factory=list, max_length=16)
    space: Literal["document", "source"] = "document"
    time: Time | None = None
    range: Span | None = None

    @model_validator(mode="after")
    def coordinates(self):
        if self.time is not None and self.range is not None:
            raise ValueError("反馈定位应使用时间点或区间其中一种")
        if self.space == "source" and not self.instance_id:
            raise ValueError("源时间反查需要明确使用实例")
        if self.instance_path and (
            self.instance_id or self.space != "document" or self.time is None
        ):
            raise ValueError("嵌套实例路径只支持文档时间点，不能同时设置直接实例")
        return self


class Selection(schemas.Strict):
    document_id: uuid.UUID
    expected_revision: int = Field(ge=1)
    instance_id: uuid.UUID
    slot: Literal["source", "mask"] = "source"


class SelectiveArgs(schemas.Strict):
    work_id: uuid.UUID
    base_revision: int = Field(ge=1)
    change_id: uuid.UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["select", "undo"]
    selections: list[Selection] = Field(min_length=1, max_length=100)


def _native_usages(doc, revision):
    if doc.kind == "image.document" and revision.schema_version == 1:
        return [
            dict(
                id=layer["id"],
                name=layer["name"],
                source=layer["source"],
                mapping=None,
                placement=Fraction(0),
                enabled=layer.get("enabled", True),
            )
            for layer in revision.payload["layers"]
        ]
    if doc.kind not in native_v2.MODELS or revision.schema_version not in {1, 2}:
        raise CapabilityUnsupported("此文档格式没有注册精确反馈时间映射")
    payload = revision.payload
    if revision.schema_version == 1:
        _, payload = native_v2.migrate(doc.kind, revision.inputs, payload)
    model = native_v2.MODELS[doc.kind].model_validate(payload)
    items = (
        [
            (c, not track.muted and not c.muted, track.name)
            for track in model.tracks
            for c in track.clips
        ]
        if doc.kind == "audio.document"
        else [(layer, layer.enabled, layer.name) for layer in model.layers]
    )
    return [
        dict(
            id=str(item.id),
            name=name,
            source=item.source,
            mapping=item.source_map,
            placement=value(item.start)
            if doc.kind == "audio.document"
            else value(item.active.start),
            enabled=enabled,
        )
        for item, enabled, name in items
    ]


def _linear(identity, source_start, duration, *, still=False):
    low, high = (Fraction(0), Fraction(1)) if still else (source_start, source_start + duration)
    return TimeMap.model_validate(
        {
            "duration": encoded(duration),
            "source_domain": {"start": encoded(low), "end": encoded(high)},
            "audio_policy": "none",
            "segments": [
                {
                    "id": str(uuid.uuid5(uuid.UUID(str(identity)), "feedback-time")),
                    "local": {"start": encoded(0), "end": encoded(duration)},
                    "source_start": encoded(0 if still else source_start),
                    "rate": encoded(0 if still else 1),
                    "hold": still,
                }
            ],
        }
    )


def _sequence_usages(doc, revision):
    if revision.schema_version != 1:
        raise CapabilityUnsupported("此剪辑文档版本没有注册反馈定位")
    frozen, editor = revision.payload["render_snapshot"], revision.payload["editor"]
    from app.modules.media.timing import duration

    duration(frozen["clips"])  # Validate the exact same transition semantics as the renderer.
    result, placement = [], Fraction(0)
    for clip, item in zip(frozen["clips"], editor["items"], strict=True):
        length = Fraction(clip["duration_ms"], 1000)
        result.append(
            dict(
                id=item["id"],
                name=item.get("note") or "画面片段",
                source=clip["usage_id"],
                placement=placement,
                enabled=True,
                mapping=_linear(
                    item["id"],
                    Fraction(clip["in_point_ms"], 1000),
                    length,
                    still=clip["output_type"] == "image",
                ),
            )
        )
        placement += length - Fraction((clip.get("transition") or {}).get("duration_ms", 0), 1000)
    for track in ("audio", "visuals"):
        for item in frozen["tracks"][track]:
            link = revision.inputs[item["usage_id"]]
            result.append(
                dict(
                    id=item["id"],
                    name=item.get("name") or track,
                    source=item["usage_id"],
                    placement=Fraction(item["start_ms"], 1000),
                    enabled=not (item.get("muted") or item.get("hidden")),
                    mapping=_linear(
                        item["id"],
                        Fraction(item.get("in_point_ms", 0), 1000),
                        Fraction(item["duration_ms"], 1000),
                        still=link["media_type"] == "image",
                    ),
                )
            )
    return result


def _coordinates(usage, args):
    mapping, placement = usage["mapping"], usage["placement"]
    if mapping is None:
        if args.time is not None or args.range is not None:
            raise CapabilityUnsupported("静态图片实例没有时间轴，请使用图层实例定位")
        return [{"space": "static"}]
    if args.time is None and args.range is None:
        raise CapabilityUnsupported("有时间轴的实例必须指定时间点或区间")
    if args.space == "source":
        matches = (
            inverse(mapping, value(args.time))
            if args.time
            else affected_ranges(mapping, value(args.range.start), value(args.range.end))
        )
        result = []
        for match in matches:
            item = {"segment_id": match["segment_id"]}
            if "point" in match:
                item["document_time"] = encoded(
                    value(Time.model_validate(match["point"])) + placement
                )
            else:
                raw = match.get("range", match)
                item["document_range"] = {
                    k: encoded(value(Time.model_validate(raw[k])) + placement)
                    for k in ("start", "end")
                }
                item["start_closed"] = match.get("start_closed", True)
                item["end_closed"] = match.get("end_closed", False)
            result.append(item)
        return result
    begin = value(args.time) if args.time else value(args.range.start)
    end = begin if args.time else value(args.range.end)
    result = []
    for segment in mapping.segments:
        left, right = value(segment.local.start) + placement, value(segment.local.end) + placement
        a, b = max(begin, left), min(end, right)
        if (args.time and not left <= begin < right) or (not args.time and a >= b):
            continue
        rate, origin = value(segment.rate), value(segment.source_start)
        start = origin + (a - left) * rate
        item = {"segment_id": str(segment.id)}
        if args.time or not rate:
            item["source_time"] = encoded(start)
            if not args.time:
                item["document_range"] = {"start": encoded(a), "end": encoded(b)}
                item["held_frame"] = True
        else:
            stop = origin + (b - left) * rate
            item.update(
                source_range={"start": encoded(min(start, stop)), "end": encoded(max(start, stop))},
                start_closed=rate > 0,
                end_closed=rate < 0,
                document_range={"start": encoded(a), "end": encoded(b)},
            )
        result.append(item)
    return result


def feedback(db, ctx, context, args):
    ctx = revalidate_context(db, ctx)
    scoped(context, args.work_id)
    service.work(db, ctx, args.work_id)
    if args.instance_path:
        # Propagate a known occurrence down a fixed path. No inverse guess and
        # no flattening of loop/hold occurrences into a made-up timestamp.
        identity, revision, point = args.document_id, args.revision, args.time
        steps, reads, root_hash = [], [], None
        for instance in args.instance_path:
            located = feedback(
                db,
                ctx,
                context,
                FeedbackArgs(
                    work_id=args.work_id,
                    document_id=identity,
                    revision=revision,
                    instance_id=instance,
                    time=point,
                ),
            )
            if root_hash is None:
                root_hash = located["content_hash"]
            if located["status"] != "located" or len(located["matches"]) != 1:
                return {
                    **located,
                    "mapped_document_id": located["document_id"],
                    "mapped_revision": located["revision"],
                    "document_id": str(args.document_id),
                    "revision": args.revision,
                    "content_hash": root_hash,
                    "instance_path": steps,
                    "read_preconditions": [*reads, *located["read_preconditions"]],
                }
            match = located["matches"][0]
            if "source_time" not in match:
                raise CapabilityUnsupported("此实例路径无法继续映射时间坐标")
            steps.append(
                {"document_id": str(identity), "revision": revision, "instance_id": str(instance)}
            )
            reads.extend(located["read_preconditions"])
            identity = uuid.UUID(match["source_reference"]["id"])
            revision = match["source_reference"]["revision"]
            point = Time.model_validate(match["source_time"])
        return {
            **located,
            "mapped_document_id": located["document_id"],
            "mapped_revision": located["revision"],
            "document_id": str(args.document_id),
            "revision": args.revision,
            "content_hash": root_hash,
            "instance_path": steps,
            "read_preconditions": reads,
        }
    doc, rev = documents.read_revision(db, args.work_id, args.document_id, args.revision)
    usages = (
        _sequence_usages(doc, rev) if doc.kind == "sequence.timeline" else _native_usages(doc, rev)
    )
    if args.instance_id:
        usages = [item for item in usages if item["id"] == str(args.instance_id)]
        if not usages:
            raise NotFound("固定文档修订中不存在此使用实例")
    matches, reads = [], [{"kind": "document", "id": str(doc.id), "revision": rev.revision}]
    for item in usages:
        coordinates = _coordinates(item, args)
        if not coordinates:
            continue
        target = rev.inputs[item["source"]]["target"]
        source, fixed = documents.read_revision(
            db, args.work_id, uuid.UUID(target["id"]), target["revision"]
        )
        reads.append({"kind": "document", "id": str(source.id), "revision": fixed.revision})
        for match in coordinates:
            matches.append(
                {
                    **match,
                    "instance_id": item["id"],
                    "name": item["name"],
                    "enabled": item["enabled"],
                    "source_reference": target,
                    "source_kind": source.kind,
                    "source_has_time_axis": not (
                        source.kind == "image.document"
                        or (
                            source.kind == "resource" and fixed.payload.get("media_type") == "image"
                        )
                    ),
                    "source_content_hash": fixed.content_hash,
                    "blob_hash": fixed.payload.get("blob_hash")
                    if source.kind == "resource"
                    else None,
                }
            )
    multiple_instances = len({m["instance_id"] for m in matches}) > 1
    inverse_ambiguous = args.space == "source" and (
        len(matches) > 1 or any("document_range" in m for m in matches)
    )
    status = (
        "outside_usage"
        if not matches
        else "ambiguous_instance"
        if multiple_instances
        else (
            "ambiguous_occurrence"
            if inverse_ambiguous
            else "mapped_range"
            if args.range
            else "located"
        )
    )
    return {
        "work_id": str(args.work_id),
        "document_id": str(doc.id),
        "revision": rev.revision,
        "content_hash": rev.content_hash,
        "space": args.space,
        "status": status,
        "matches": matches,
        "read_preconditions": reads,
        "current_revision_assumed": False,
        "limitations": [
            "坐标固定于指定修订；不自动迁移到后续剪辑",
            "重叠画面和重复使用返回全部匹配，需要明确选择",
        ],
    }


def _references(db, work_id, ref):
    doc, rev = documents.read_revision(db, work_id, uuid.UUID(ref["id"]), ref["revision"])
    result = instances.references(
        {
            "kind": doc.kind,
            "schema_version": rev.schema_version,
            "inputs": rev.inputs,
            "payload": rev.payload,
        }
    )
    if result is None:
        raise CapabilityUnsupported(
            "选择性引用采用/撤销仅支持原生图片、音频及合成实例；剪辑参数需单独生成编辑候选"
        )
    return result


def selective_edit(db, ctx, context, args):
    from app.modules.engineering import review
    from app.modules.harness.editing_tools import _execution
    from app.modules.harness.read_tracking import merge

    ctx = revalidate_context(db, ctx)
    require(ctx.role, "engineering.edit")
    scoped(context, args.work_id)
    key, provenance = _execution(db, ctx, context)
    service.work(db, ctx, args.work_id, lock=True)
    original = review.get_change(db, args.work_id, args.change_id)
    if original.fingerprint != args.fingerprint:
        raise Conflict("待选择的候选内容已变化")
    if args.mode == "undo" and original.status != "accepted":
        raise Conflict("只可撤销已经采用的变更")
    if args.mode == "select" and original.status not in {"proposed", "accepted"}:
        raise Conflict("此候选不可用于选择性修改")
    before = service.snapshot_at(db, args.work_id, original.base_revision)
    after = original.snapshot
    base = service.snapshot_at(db, args.work_id, args.base_revision)
    by_doc, seen, selected = {}, set(), []
    for item in args.selections:
        identity, slot = str(item.document_id), (str(item.instance_id), item.slot)
        if (identity, slot) in seen:
            raise Conflict("同一使用位置只能选择一次")
        seen.add((identity, slot))
        old, new, current = [s.get("native_roots", {}).get(identity) for s in (before, after, base)]
        if not old or not new or not current:
            raise CapabilityUnsupported(
                "此选择需要变更前后都存在的直接输出实例；新增删除及嵌套派生须另建明确候选"
            )
        if current["revision"] != item.expected_revision:
            raise Conflict("所选输出已变化，请读取当前固定修订后再修改")
        old_ref, new_ref, now_ref = [
            _references(db, args.work_id, r).get(slot) for r in (old, new, current)
        ]
        if not old_ref or not new_ref or old_ref == new_ref:
            raise CapabilityUnsupported("所选位置不是可独立采用或撤销的引用替换")
        expected, target = (new_ref, old_ref) if args.mode == "undo" else (old_ref, new_ref)
        if now_ref != expected:
            raise Conflict("所选位置已有其他修改，不能覆盖；请重新决定要保留的内容")
        by_doc.setdefault(identity, (item.expected_revision, []))[1].append(
            schemas.ReplaceInstanceInput(
                instance_id=item.instance_id, slot=item.slot, expected=expected, target=target
            )
        )
        selected.append(
            {
                "document_id": identity,
                "instance_id": slot[0],
                "slot": slot[1],
                "before": expected,
                "after": target,
            }
        )
    commands = [
        schemas.RetargetInstances(
            operation="instance.retarget",
            document_id=identity,
            expected_revision=revision,
            instances=items,
        )
        for identity, (revision, items) in by_doc.items()
    ]
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
    return {
        "change_id": str(change["id"]),
        "work_id": str(args.work_id),
        "fingerprint": change["fingerprint"],
        "status": change["status"],
        "base_revision": args.base_revision,
        "adopted": False,
        "requires_review": True,
        "mode": args.mode,
        "selections": selected,
        "source_change_id": str(args.change_id),
        "limits": [
            "只替换所选实例的source/mask引用，保留其他实例及当前参数",
            "不自动采用、不恢复整个工程、不隐式撤销时间或同步变更",
        ],
    }


def register_tools():
    for name, description, schema, handler, effect in [
        (
            "engineering.feedback",
            "按固定工程修订定位预览反馈：实例、文档/源时间点、区间及显式嵌套路径；重复或重叠返回歧义。",
            FeedbackArgs,
            feedback,
            "read",
        ),
        (
            "engineering.selective_edit",
            "选择性采用/撤销固定候选中的原生实例source/mask替换，产生新的待审候选，保护其他手改；不支持任意参数撤销。",
            SelectiveArgs,
            selective_edit,
            "candidate",
        ),
    ]:
        if name in registry.CATALOG:
            continue
        registry.register(registry.Tool(name, description, schema, group="engineering"), handler)
        capabilities.register_contract(
            name,
            ToolPolicy(
                effect=effect,
                scope="work",
                permissions=("engineering.edit",) if effect == "candidate" else (),
                idempotency="business_key" if effect == "candidate" else "read_only",
                cancellation="not_applicable",
                recovery="same_request" if effect == "candidate" else "repeat_read",
                output_kind="editable_engineering_candidate"
                if effect == "candidate"
                else "fixed_feedback_location",
                output_schema={
                    "type": "object",
                    "required": (
                        ["change_id", "fingerprint", "adopted", "selections"]
                        if effect == "candidate"
                        else [
                            "document_id",
                            "revision",
                            "content_hash",
                            "status",
                            "matches",
                            "read_preconditions",
                        ]
                    ),
                },
            ),
        )
