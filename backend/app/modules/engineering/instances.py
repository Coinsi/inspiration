"""Versioned instance adapters: isolate selected usages without mutating their source."""

import copy
import uuid

from app.core.errors import CapabilityUnsupported, Conflict
from app.modules.engineering import documents, schemas

_ADAPTERS = {}


def register(kind, version, enumerate_instances):
    """Trusted adapters yield (instance dict, supported input slots), never arbitrary paths."""
    key = (kind, version)
    if key in _ADAPTERS:
        raise ValueError("Instance adapter already registered")
    _ADAPTERS[key] = enumerate_instances


def layers(payload):
    return [(layer, {"source", "mask"}) for layer in payload["layers"]]


def clips(payload):
    return [(clip, {"source"}) for track in payload["tracks"] for clip in track["clips"]]


register("image.document", 1, layers)
register("composition.document", 1, layers)
register("audio.document", 1, clips)
register("composition.document", 2, layers)
register("audio.document", 2, clips)


def references(node):
    """Expose stable instance locations for review; unknown schemas stay opaque."""
    adapter = _ADAPTERS.get((node["kind"], node["schema_version"]))
    if not adapter:
        return None
    return {
        (str(item["id"]), slot): node["inputs"][item[slot]]["target"]
        for item, slots in adapter(node["payload"])
        for slot in slots
        if item.get(slot) in node["inputs"]
    }


def labels(node):
    adapter = _ADAPTERS.get((node["kind"], node["schema_version"]))
    if not adapter:
        return {}
    return {
        str(item["id"]): item.get("name") or f"片段 {index + 1}"
        for index, (item, _) in enumerate(adapter(node["payload"]))
    }


def retarget(db, work_id, command):
    doc, fixed = documents.read_revision(
        db, work_id, command.document_id, command.expected_revision
    )
    enumerate_instances = _ADAPTERS.get((doc.kind, fixed.schema_version))
    if not enumerate_instances:
        raise CapabilityUnsupported("此文档类型尚未注册使用实例编辑适配器")
    inputs, payload = copy.deepcopy(fixed.inputs), copy.deepcopy(fixed.payload)
    instances = {str(item["id"]): (item, slots) for item, slots in enumerate_instances(payload)}
    seen = set()
    for replacement in command.instances:
        identity, slot = str(replacement.instance_id), replacement.slot
        if (identity, slot) in seen:
            raise Conflict("一次变更不能重复修改同一个实例输入")
        seen.add((identity, slot))
        selected = instances.get(identity)
        if not selected or slot not in selected[1]:
            raise Conflict("指定实例或输入位置不存在")
        item = selected[0]
        old = inputs.get(item.get(slot))
        if not old or old["target"] != replacement.expected.model_dump(mode="json"):
            raise Conflict("实例原引用已变化，请重新读取后修改")
        # A fresh port affects only this instance/slot. Leave original inputs in
        # place: another instance or extension may still reference them.
        alias = "usage_" + uuid.uuid4().hex
        inputs[alias] = {**copy.deepcopy(old), "target": replacement.target.model_dump(mode="json")}
        item[slot] = alias
    return schemas.PutNativeDocument(
        operation="document.put",
        document_id=doc.id,
        expected_revision=command.expected_revision,
        kind=doc.kind,
        schema_version=fixed.schema_version,
        title=doc.title,
        inputs=inputs,
        payload=payload,
    )


def retarget_path(db, work_id, command):
    """Copy the selected parent chain; other occurrences keep the old fixed refs."""
    chain = []
    identity, revision = command.document_id, command.expected_revision
    for step in command.path:
        # Reuse the same adapter and precondition checks at every path segment.
        prepared = retarget(
            db,
            work_id,
            schemas.RetargetInstances(
                operation="instance.retarget",
                document_id=identity,
                expected_revision=revision,
                instances=[
                    schemas.ReplaceInstanceInput(
                        instance_id=step.instance_id,
                        slot=step.slot,
                        expected=step.expected,
                        target=step.expected,
                    )
                ],
            ),
        )
        chain.append((prepared, step))
        identity, revision = step.expected.id, step.expected.revision
    target = command.target.model_dump(mode="json")
    commands, forks = [], []
    for index in range(len(chain) - 1, -1, -1):
        prepared, step = chain[index]
        adapter = _ADAPTERS[(prepared.kind, prepared.schema_version)]
        item = next(
            item
            for item, _ in adapter(prepared.payload)
            if str(item["id"]) == str(step.instance_id)
        )
        prepared.inputs[item[step.slot]]["target"] = target
        if index:
            original = {"id": str(prepared.document_id), "revision": prepared.expected_revision}
            prepared.document_id = uuid.uuid4()
            prepared.expected_revision = 0
            prepared.publish_output = False
            forks.append({"document_id": str(prepared.document_id), "derived_from": original})
        commands.append(prepared)
        target = {"candidate_document": str(prepared.document_id)}
    return commands, forks
