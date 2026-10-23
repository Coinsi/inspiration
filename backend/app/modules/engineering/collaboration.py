"""Conservative candidate prerequisites and replay checks.

Read/write scopes are server derived and may only be widened by callers. They are
not an authorization mechanism. Timed, restoration and migration operations are
kept reviewable but deliberately excluded from automatic replay.
"""

import copy
import uuid

from sqlalchemy import select

from app.core.errors import Conflict, NotFound
from app.models.timeline import TimelineRevision
from app.modules.engineering import documents, schemas
from app.modules.timeline import editing


def nodes(db, work_id, snapshot):
    return (
        documents.closure(db, work_id, snapshot["manifest_ref"])
        if snapshot.get("manifest_ref")
        else []
    )


def document_values(graph, identity):
    return sorted(
        [
            {
                "revision": node["revision"],
                "content_hash": documents.digest(node["inputs"], node["payload"]),
            }
            for node in graph
            if node["id"] == identity
        ],
        key=lambda item: item["revision"],
    )


def usage_value(snapshot, identity):
    return next(
        (copy.deepcopy(item) for item in snapshot["resources"] if item["usage_id"] == identity),
        None,
    )


def timeline_baseline(db, ctx, timeline_id, revision):
    current = editing.lock(db, ctx.project.id, timeline_id)
    if revision == current.revision:
        return editing.document(db, ctx.project.id, timeline_id)
    old = db.scalar(
        select(TimelineRevision).where(
            TimelineRevision.timeline_id == timeline_id,
            TimelineRevision.revision == revision,
        )
    )
    if not old:
        raise Conflict("找不到固定时间线基线，请保留草稿并重新读取历史版本")
    return copy.deepcopy(old.snapshot)


def capture_contract(db, ctx, work_id, base, data, timeline_updates):
    graph = nodes(db, work_id, base)
    reads, writes = {}, {}

    def add(kind, identity, value, origin):
        key = (kind, str(identity))
        if key in reads:
            if reads[key]["value"] != value:
                raise Conflict("同一对象的读取前提不一致")
            reads[key]["origins"] = sorted(set([*reads[key]["origins"], origin]))
        else:
            reads[key] = {"kind": kind, "id": str(identity), "value": value, "origins": [origin]}

    def dependency(reference, origin):
        for node in documents.closure(db, work_id, reference):
            add("document", node["id"], document_values(graph, node["id"]), origin)
            fixed = {
                "revision": node["revision"],
                "content_hash": documents.digest(node["inputs"], node["payload"]),
            }
            references = reads[("document", node["id"])].setdefault("fixed_references", [])
            if fixed not in references:
                references.append(fixed)
                references.sort(key=lambda item: item["revision"])

    def write(kind, identity, value):
        writes[(kind, str(identity))] = {
            "kind": kind,
            "id": str(identity),
            "value": copy.deepcopy(value),
        }

    for command in data.commands:
        if isinstance(command, (schemas.Add, schemas.Remove)):
            value = usage_value(base, str(command.usage_id))
            add("resource_usage", command.usage_id, value, "command")
            write("resource_usage", command.usage_id, value)
        elif isinstance(command, schemas.PutNativeDocument):
            prior = base.get("native_roots", {}).get(str(command.document_id))
            write("native_root", command.document_id, prior)
            if prior:
                dependency(prior, "edited_document")
            for link in command.inputs.values():
                target = link.get("target") if isinstance(link, dict) else None
                if isinstance(target, dict) and "id" in target:
                    dependency(target, "input_dependency")
        else:
            # These commands can involve restores, path copies or shared time domains.
            add("work", work_id, data.base_revision, "conservative_command")
            write("work", work_id, data.base_revision)
    for update in timeline_updates:
        tid, revision = uuid.UUID(update["timeline_id"]), update["expected_revision"]
        add("timeline", tid, revision, "timeline_edit")
        write("timeline", tid, revision)
    for declared in data.read_preconditions:
        identity = str(declared.id)
        if declared.kind == "work":
            if declared.id != work_id or declared.revision not in (None, data.base_revision):
                raise Conflict("读取的作品基线不一致")
            value = data.base_revision
        elif declared.kind == "document":
            value = document_values(graph, identity)
            if declared.revision is not None:
                documents.read_revision(db, work_id, declared.id, declared.revision)
                dependency({"id": identity, "revision": declared.revision}, "declared_dependency")
            elif not value:
                raise NotFound("读取的文档不在固定工程基线中，请提供固定修订")
            else:
                for ref in value:
                    dependency({"id": identity, "revision": ref["revision"]}, "declared_dependency")
        elif declared.kind == "timeline":
            if declared.revision is None:
                raise Conflict("时间线读取必须指定固定修订")
            timeline_baseline(db, ctx, declared.id, declared.revision)
            value = declared.revision
        else:
            if declared.revision is not None:
                raise Conflict("素材使用前提采用工程基线，不接受独立修订")
            value = usage_value(base, identity)
            if value is None:
                raise NotFound("读取的素材使用不在工程基线中")
        add(declared.kind, declared.id, value, "declared")
        revisions = reads[(declared.kind, identity)].setdefault("declared_revisions", [])
        if declared.revision not in revisions:
            revisions.append(declared.revision)
    return sorted(reads.values(), key=lambda x: (x["kind"], x["id"])), sorted(
        writes.values(), key=lambda x: (x["kind"], x["id"])
    )


def conflicts(db, ctx, work, change, current):
    graph = nodes(db, work.id, current)
    result = []
    for read in change.read_preconditions:
        kind, identity = read["kind"], read["id"]
        if kind == "work":
            value = work.engineering_revision
        elif kind == "document":
            value = document_values(graph, identity)
        elif kind == "resource_usage":
            value = usage_value(current, identity)
        else:
            value = editing.lock(db, ctx.project.id, uuid.UUID(identity)).revision
        if value != read["value"]:
            result.append(
                {
                    "kind": kind,
                    "id": identity,
                    "reason": "read_changed",
                    "expected": read["value"],
                    "actual": value,
                }
            )
    for scope in change.write_scopes:
        if scope["kind"] == "native_root":
            value = current.get("native_roots", {}).get(scope["id"])
            if value != scope["value"]:
                result.append(
                    {
                        "kind": "document",
                        "id": scope["id"],
                        "reason": "write_changed",
                        "expected": scope["value"],
                        "actual": value,
                    }
                )
    return result


def replay_commands(db, work, change, current):
    if not change.canonical_commands or not change.write_scopes:
        raise Conflict("旧候选没有完整操作契约，请基于当前工程重新提出修改")
    data = schemas.Propose.model_validate(
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": work.engineering_revision,
            "commands": change.canonical_commands,
        }
    )
    allowed = (schemas.Add, schemas.Remove, schemas.PutNativeDocument)
    if any(not isinstance(command, allowed) for command in data.commands):
        raise Conflict(
            "该操作涉及时间线、恢复或引用调整，不能自动整合，请重新提出候选",
            {"reason": "replay_unsupported"},
        )
    if any(isinstance(command, schemas.PutNativeDocument) for command in data.commands):
        graph = [*nodes(db, work.id, current), *nodes(db, work.id, change.snapshot)]
        if any(
            node["kind"] == "sync.document" or node["payload"].get("sync") for node in graph
        ) or any(
            isinstance(command, schemas.PutNativeDocument)
            and command.kind in {"audio.document", "composition.document", "sync.document"}
            for command in data.commands
        ):
            raise Conflict(
                "存在时间或同步依赖，须重新检查后提出整合候选",
                {"reason": "temporal_review_required"},
            )
        if any(
            isinstance(command, schemas.PutNativeDocument) and not command.publish_output
            for command in data.commands
        ):
            raise Conflict("派生的隐藏文档须由专用整合器处理", {"reason": "replay_unsupported"})
    return data.commands
