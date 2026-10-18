"""Version 2 timed documents: exact maps and explicit synchronization constraints.

These are persistent editable contracts. No v1 executor may silently flatten them.
"""

import copy
import uuid
from typing import Literal

from pydantic import Field, model_validator

from app.modules.engineering import native
from app.modules.engineering.timing import Rate, TimeMap, encoded, value


class AudioClip(native.Strict):
    id: uuid.UUID
    source: str
    source_map: TimeMap
    start: native.Time
    gain_db: float = Field(default=0, ge=-120, le=24)
    muted: bool = False


class AudioTrack(native.AudioTrack):
    clips: list[AudioClip] = Field(default_factory=list, max_length=2000)


class TimedLayer(native.Layer):
    active: native.Span
    source_map: TimeMap

    @model_validator(mode="after")
    def duration_matches(self):
        if value(self.active.end) - value(self.active.start) != value(self.source_map.duration):
            raise ValueError("图层使用时长与时间映射不一致")
        return self


class Anchor(native.Strict):
    instance_id: uuid.UUID
    local: native.Time
    expected_source_input: str | None = None


class SyncConstraint(native.Strict):
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    relation: Literal["offset", "shared_map", "semantic_alignment"]
    policy: Literal["hard", "review"]
    members: list[Anchor] = Field(min_length=2, max_length=128)
    offset: Rate = Field(default_factory=lambda: Rate(numerator=0, denominator=1))
    tolerance: native.Time = Field(default_factory=lambda: native.Time(numerator=0, denominator=1))
    evidence_input: str | None = None
    expected_evidence_input: str | None = None


class AudioDocument(native.AudioDocument):
    tracks: list[AudioTrack] = Field(default_factory=list, max_length=128)
    sync: list[SyncConstraint] = Field(default_factory=list, max_length=256)


class CompositionDocument(native.CompositionDocument):
    layers: list[TimedLayer] = Field(default_factory=list, max_length=2000)
    sync: list[SyncConstraint] = Field(default_factory=list, max_length=256)


MODELS = {"audio.document": AudioDocument, "composition.document": CompositionDocument}


def review_items(nodes):
    nodes = list(nodes)
    result = []
    for node in nodes:
        if node["kind"] not in MODELS or node["schema_version"] != 2:
            continue
        doc = MODELS[node["kind"]].model_validate(node["payload"])
        for issue in sync_issues(doc, node["inputs"]):
            result.append(
                {
                    **issue,
                    "key": f"{node['id']}:{node['revision']}:{issue['id']}",
                    "document_id": node["id"],
                    "document_title": node["title"],
                }
            )
    from app.modules.engineering import work_sync

    result.extend(work_sync.review_items(nodes))
    return result


def sync_issues(document, inputs, *, entries=None):
    entries = (
        entries
        if entries is not None
        else (
            {str(c.id): (c, value(c.start)) for t in document.tracks for c in t.clips}
            if isinstance(document, AudioDocument)
            else {str(layer.id): (layer, value(layer.active.start)) for layer in document.layers}
        )
    )
    issues, seen = [], set()
    for constraint in document.sync:
        if constraint.id in seen:
            raise ValueError("同步约束身份重复")
        seen.add(constraint.id)
        points, maps, reasons, member_ids = [], [], [], set()
        for anchor in constraint.members:
            identity = str(anchor.instance_id)
            if identity in member_ids or identity not in entries:
                raise ValueError("同步约束引用重复或不存在的使用实例")
            member_ids.add(identity)
            item, start = entries[identity]
            if value(anchor.local) > value(item.source_map.duration):
                reasons.append("同步锚点超出使用时长")
            points.append(start + value(anchor.local))
            mapping = item.source_map
            temporal = (
                value(mapping.duration),
                value(mapping.source_domain.start),
                value(mapping.source_domain.end),
                tuple(
                    (
                        value(s.local.start),
                        value(s.local.end),
                        value(s.source_start),
                        value(s.rate),
                        s.join,
                        s.hold,
                    )
                    for s in mapping.segments
                ),
            )
            maps.append(temporal)
            if constraint.relation == "semantic_alignment":
                baseline = inputs.get(anchor.expected_source_input)
                if baseline is None or anchor.expected_source_input == item.source:
                    raise ValueError("语义对齐必须固定每个来源修订")
                if inputs[item.source]["target"] != baseline["target"]:
                    reasons.append("来源修订已改变，需要重新检查语义对齐")
        if any(
            abs(point - points[0] - value(constraint.offset)) > value(constraint.tolerance)
            for point in points[1:]
        ):
            reasons.append("同步锚点间距超出允许偏差")
        if constraint.relation == "shared_map" and any(item != maps[0] for item in maps[1:]):
            reasons.append("共享时间映射已分离")
        if constraint.relation == "semantic_alignment":
            evidence = inputs.get(constraint.evidence_input)
            baseline = inputs.get(constraint.expected_evidence_input)
            if (
                not evidence
                or not baseline
                or constraint.evidence_input == constraint.expected_evidence_input
            ):
                raise ValueError("语义对齐必须固定对齐证据输入")
            if evidence["target"] != baseline["target"]:
                reasons.append("对齐证据已更新，需要重新检查")
        if reasons:
            issues.append(
                {
                    "id": str(constraint.id),
                    "name": constraint.name,
                    "policy": constraint.policy,
                    "reasons": list(dict.fromkeys(reasons)),
                }
            )
    return issues


def validate(kind, inputs, payload):
    document = MODELS[kind].model_validate(payload)
    native.validate_inputs(inputs, document)
    if isinstance(document, AudioDocument):
        if any(c.source_map.audio_policy == "none" for t in document.tracks for c in t.clips):
            raise ValueError("音频片段必须明确声音处理策略")
    issues = sync_issues(document, inputs)
    if any(issue["policy"] == "hard" for issue in issues):
        raise ValueError(
            "修改违反硬同步约束：" + "；".join(i["name"] for i in issues if i["policy"] == "hard")
        )
    return document


def migrate(kind, inputs, payload):
    """Explicit lossless v1 -> v2, preserving positions and source coordinates."""
    payload = native.MODELS[kind].model_validate(copy.deepcopy(payload)).model_dump(mode="json")
    if kind == "audio.document":
        entries = [c for track in payload.get("tracks", []) for c in track["clips"]]
    else:
        entries = payload.get("layers", [])
    for item in entries:
        if kind == "audio.document":
            span = native.Span.model_validate(item.pop("source_range"))
            start, end, policy = value(span.start), value(span.end), "resample"
        else:
            start = value(native.Time.model_validate(item.pop("source_start")))
            active = native.Span.model_validate(item["active"])
            end, policy = start + value(active.end) - value(active.start), "none"
        duration = end - start
        item["source_map"] = {
            "duration": encoded(duration),
            "source_domain": {"start": encoded(start), "end": encoded(end)},
            "audio_policy": policy,
            "segments": [
                {
                    "id": str(uuid.uuid5(uuid.UUID(item["id"]), "time-map-v1")),
                    "local": {"start": encoded(0), "end": encoded(duration)},
                    "source_start": encoded(start),
                    "rate": encoded(1),
                    "join": "cut",
                    "hold": False,
                }
            ],
        }
    payload["sync"] = []
    return inputs, payload
