"""Cross-document sync is a versioned engineering document, not agent state.

All documents/evidence are fixed public inputs so package remapping is generic.
Member documents must also be current output roots: an old pinned constraint
cannot silently claim a newly edited output is still synchronized.
"""

import copy
import uuid
from types import SimpleNamespace

from pydantic import Field

from app.core.errors import CapabilityUnsupported
from app.modules.engineering import native, native_v2
from app.modules.engineering.timing import Rate, value

KIND = "sync.document"
MAX_MEMBERS = 4096


class Anchor(native_v2.Anchor):
    document_input: str = Field(min_length=1, max_length=255)
    document_offset: Rate = Field(default_factory=lambda: Rate(numerator=0, denominator=1))


class Constraint(native_v2.SyncConstraint):
    members: list[Anchor] = Field(min_length=2, max_length=128)


class Document(native.Strict):
    constraints: list[Constraint] = Field(default_factory=list, max_length=256)


def validate(inputs, payload):
    links = {alias: native.Input.model_validate(link) for alias, link in inputs.items()}
    doc = Document.model_validate(payload)
    seen = set()
    for constraint in doc.constraints:
        if constraint.id in seen:
            raise ValueError("跨文档同步约束身份重复")
        seen.add(constraint.id)
        members = set()
        for member in constraint.members:
            link = links.get(member.document_input)
            if link is None or link.accepts not in native_v2.MODELS:
                raise ValueError("同步成员必须固定引用原生音频或合成文档")
            key = (link.target.id, member.instance_id)
            if key in members:
                raise ValueError("跨文档同步不能通过不同别名重复引用同一实例")
            members.add(key)
            if constraint.relation == "semantic_alignment":
                if member.expected_source_input not in links:
                    raise ValueError("跨文档语义对齐缺少固定来源基线")
        if constraint.relation == "semantic_alignment" and (
            constraint.evidence_input not in links
            or constraint.expected_evidence_input not in links
            or constraint.evidence_input == constraint.expected_evidence_input
        ):
            raise ValueError("跨文档语义对齐缺少独立的固定证据基线")
    return doc


def review_items(nodes):
    from app.modules.engineering.documents import digest

    nodes = list(nodes)
    lookup = {(node["id"], node["revision"]): node for node in nodes}
    # The closure root is last; choose that manifest, never a nested historical manifest.
    manifests = [node for node in nodes if node["kind"] == "work.manifest"]
    if not manifests:
        if any(node["kind"] == KIND for node in nodes):
            raise CapabilityUnsupported("跨文档同步检查需要作品输出清单")
        return []
    manifest = manifests[-1]
    roots = {
        link["target"]["id"]: link["target"]
        for output in manifest["payload"]["outputs"].values()
        for link in [manifest["inputs"][output["input"]]]
    }
    for output in manifest["payload"]["outputs"].values():
        ref = manifest["inputs"][output["input"]]["target"]
        if roots[ref["id"]] != ref and any(node["kind"] == KIND for node in nodes):
            raise CapabilityUnsupported("同一文档有多个输出修订，不能猜测同步绑定")
    result, parsed = [], {}
    member_count = 0
    for node in nodes:
        if node["kind"] != KIND:
            continue
        if node["schema_version"] != 1:
            raise CapabilityUnsupported("此跨文档同步格式尚不能验证")
        doc = validate(node["inputs"], node["payload"])
        for constraint in doc.constraints:
            member_count += len(constraint.members)
            if member_count > MAX_MEMBERS:
                raise CapabilityUnsupported("跨文档同步成员超过当前验证预算")
            links = copy.deepcopy(node["inputs"])
            entries, members, reasons = {}, [], []
            bound = {}
            for index, member in enumerate(constraint.members):
                ref = links[member.document_input]["target"]
                current = roots.get(ref["id"])
                bound[member.document_input] = current
                if current != ref:
                    reasons.append("同步成员的固定修订未与当前作品输出一致")
                effective = current or ref
                target = lookup[(effective["id"], effective["revision"])]
                key = (effective["id"], effective["revision"])
                if key not in parsed:
                    payload = target["payload"]
                    if target["kind"] not in native_v2.MODELS or target["schema_version"] not in (
                        1,
                        2,
                    ):
                        raise CapabilityUnsupported("同步成员的时间格式尚不能验证")
                    if target["schema_version"] == 1:
                        _, payload = native_v2.migrate(target["kind"], target["inputs"], payload)
                    parsed[key] = native_v2.MODELS[target["kind"]].model_validate(payload)
                model = parsed[key]
                items = (
                    [clip for track in model.tracks for clip in track.clips]
                    if target["kind"] == "audio.document"
                    else model.layers
                )
                item = next((item for item in items if item.id == member.instance_id), None)
                if item is None:
                    reasons.append("同步成员的使用实例已移除")
                    continue
                identity = uuid.uuid5(
                    constraint.id, f"{member.document_input}:{member.instance_id}"
                )
                alias = f"__member_source_{index}"
                while alias in links:
                    alias += "_"
                links[alias] = target["inputs"][item.source]
                start = (
                    value(item.start)
                    if target["kind"] == "audio.document"
                    else value(item.active.start)
                )
                entries[str(identity)] = (
                    item.model_copy(update={"source": alias}),
                    start + value(member.document_offset),
                )
                members.append(
                    native_v2.Anchor(
                        instance_id=identity,
                        local=member.local,
                        expected_source_input=member.expected_source_input,
                    )
                )
            if len(members) == len(constraint.members):
                normalized = native_v2.SyncConstraint.model_validate(
                    {**constraint.model_dump(exclude={"members"}), "members": members}
                )
                issues = native_v2.sync_issues(
                    SimpleNamespace(sync=[normalized]), links, entries=entries
                )
                for issue in issues:
                    reasons.extend(issue["reasons"])
            if reasons:
                if constraint.policy == "hard":
                    raise CapabilityUnsupported(
                        "修改违反跨文档硬同步约束",
                        {"name": constraint.name, "reasons": list(dict.fromkeys(reasons))},
                    )
                result.append(
                    {
                        "id": str(constraint.id),
                        "name": constraint.name,
                        "policy": constraint.policy,
                        "reasons": list(dict.fromkeys(reasons)),
                        "document_id": node["id"],
                        "document_title": node["title"],
                        "key": (
                            f"{node['id']}:{node['revision']}:{constraint.id}:{digest({}, bound)}"
                        ),
                    }
                )
    return result
