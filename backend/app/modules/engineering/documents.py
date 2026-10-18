"""Versioned engineering documents; the timeline adapter is one supported kind.

Immutable revisions are never updated. Working timeline rows remain the sole editing
projection; candidates and commits resolve fixed document revisions, never latest.
"""

import copy
import hashlib
import json
import uuid

from sqlalchemy import func, select

from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.engineering import EngineeringDocument as Document
from app.models.engineering import EngineeringDocumentRevision as Revision
from app.models.timeline import Timeline
from app.modules.engineering import document_types

SUPPORTED = document_types.CURRENT


def digest(inputs, payload):
    return hashlib.sha256(
        json.dumps(
            {"inputs": inputs, "payload": payload},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def append(
    db,
    ctx,
    work_id,
    document_id,
    kind,
    title,
    inputs,
    payload,
    timeline_id=None,
    schema_version=None,
    preserve_unknown=False,
):
    if kind not in SUPPORTED and not preserve_unknown:
        raise CapabilityUnsupported("没有注册该编辑文档的校验器和执行适配器")
    schema_version = schema_version or SUPPORTED.get(kind)
    try:
        if preserve_unknown:
            document_types.validate_stored(kind, schema_version, inputs, payload)
        else:
            document_types.adapter(kind, schema_version).validate(inputs, payload)
    except ValueError as exc:
        raise CapabilityUnsupported("工程文档结构不合法", {"reason": str(exc)}) from exc
    doc = db.get(Document, document_id)
    if doc and (
        doc.work_id != work_id or doc.kind != kind or doc.source_timeline_id != timeline_id
    ):
        raise Conflict("文档身份与类型或归属不一致")
    if not doc:
        doc = Document(
            id=document_id, work_id=work_id, kind=kind, title=title, source_timeline_id=timeline_id
        )
        db.add(doc)
        db.flush()
    fingerprint = digest(inputs, payload)
    prior = db.scalar(
        select(Revision)
        .where(
            Revision.document_id == doc.id,
            Revision.content_hash == fingerprint,
            Revision.schema_version == schema_version,
        )
        .order_by(Revision.revision.desc())
    )
    if prior:
        return {"id": str(doc.id), "revision": prior.revision}
    number = (
        db.scalar(select(func.max(Revision.revision)).where(Revision.document_id == doc.id)) or 0
    ) + 1
    db.add(
        Revision(
            document_id=doc.id,
            revision=number,
            schema_version=schema_version,
            content_hash=fingerprint,
            inputs=copy.deepcopy(inputs),
            payload=copy.deepcopy(payload),
            created_by=ctx.user.id,
        )
    )
    db.flush()
    return {"id": str(doc.id), "revision": number}


def read_revision(db, work_id, document_id, revision):
    doc = db.get(Document, document_id)
    if not doc or doc.work_id != work_id:
        raise NotFound("工程文档不存在或不属于此作品")
    row = db.get(Revision, (document_id, revision))
    if not row:
        raise NotFound("文档修订不存在")
    document_types.validate_stored(doc.kind, row.schema_version, row.inputs, row.payload)
    if digest(row.inputs, row.payload) != row.content_hash:
        raise Conflict("工程文档内容校验失败")
    return doc, row


def store_snapshot(db, ctx, work_id, snapshot, aspects=None):
    """Create resource leaves then sequence roots atomically with the candidate."""
    resource_refs = {}
    for item in snapshot["resources"]:
        resource_id = uuid.uuid5(
            work_id, "resource:" + item["source_kind"] + ":" + item["source_id"]
        )
        resource_refs[item["usage_id"]] = append(
            db,
            ctx,
            work_id,
            resource_id,
            "resource",
            item["name"],
            {},
            {
                "source_kind": item["source_kind"],
                "source_id": item["source_id"],
                "resource_id": item["resource_id"],
                "blob_hash": item["blob_hash"],
                "media_type": item["media_type"],
            },
        )
    snapshot["resource_roots"] = resource_refs
    refs = copy.deepcopy(snapshot.get("document_roots", {}))
    for tid, capture in snapshot.get("timeline_captures", {}).items():
        timeline_id = uuid.UUID(tid)
        inputs = {}
        for item in snapshot["resources"]:
            if item.get("timeline_capture") != tid:
                continue
            inputs[item["usage_id"]] = {
                "target": resource_refs[item["usage_id"]],
                "media_type": item["media_type"],
            }
        editor = copy.deepcopy(capture["document"])
        editor.pop("revision", None)
        frozen = copy.deepcopy(capture["render_snapshot"])
        frozen["tracks"].pop("timeline_revision", None)
        existing = refs.get(tid)
        aspect = (aspects or {}).get(tid)
        if not aspect and existing:
            _, previous = read_revision(
                db, work_id, uuid.UUID(existing["id"]), existing["revision"]
            )
            aspect = previous.payload["aspect_ratio"]
        t = db.get(Timeline, timeline_id)
        refs[tid] = append(
            db,
            ctx,
            work_id,
            uuid.uuid5(work_id, "sequence:" + tid),
            "sequence.timeline",
            t.name if t else "剪辑文档",
            inputs,
            {"editor": editor, "render_snapshot": frozen, "aspect_ratio": aspect or "16:9"},
            timeline_id,
        )
    snapshot["document_roots"] = refs
    inputs = {
        "sequence:" + tid: {"target": ref, "accepts": "sequence.timeline"}
        for tid, ref in refs.items()
    }
    inputs.update(
        {
            "resource:" + usage: {"target": ref, "accepts": "resource"}
            for usage, ref in resource_refs.items()
        }
    )
    outputs = {tid: {"input": "sequence:" + tid, "streams": ["picture", "sound"]} for tid in refs}
    for key, ref in snapshot.get("native_roots", {}).items():
        doc, content = read_revision(db, work_id, uuid.UUID(ref["id"]), ref["revision"])
        port = "document:" + key
        inputs[port] = {"target": ref, "accepts": doc.kind}
        outputs[key] = {
            "input": port,
            "streams": list(document_types.adapter(doc.kind, content.schema_version).output_streams)
            if document_types.available(doc.kind, content.schema_version)
            else snapshot.get("preserved_output_streams", {}).get(key, []),
        }
        snapshot.setdefault("preserved_output_streams", {})[key] = outputs[key]["streams"]
    snapshot["manifest_ref"] = append(
        db,
        ctx,
        work_id,
        uuid.uuid5(work_id, "manifest"),
        "work.manifest",
        "作品工程清单",
        inputs,
        {
            "format": 1,
            "outputs": outputs,
        },
    )
    snapshot["document_format"] = 1
    return refs


def compile_sequence(db, work_id, reference):
    doc, row = read_revision(db, work_id, uuid.UUID(reference["id"]), reference["revision"])
    if doc.kind != "sequence.timeline":
        raise CapabilityUnsupported("此文档不支持剪辑渲染")
    frozen = copy.deepcopy(row.payload["render_snapshot"])
    leaves = [*frozen["clips"], *frozen["tracks"]["audio"], *frozen["tracks"]["visuals"]]
    used = set()
    for item in leaves:
        usage = item.get("usage_id")
        link = row.inputs.get(usage)
        if not link:
            raise Conflict("工程文档缺少固定素材引用")
        child, resource = read_revision(
            db, work_id, uuid.UUID(link["target"]["id"]), link["target"]["revision"]
        )
        if child.kind != "resource" or resource.inputs:
            raise CapabilityUnsupported("当前剪辑适配器只接受固定媒体资源输入")
        if (
            item["blob_hash"] != resource.payload["blob_hash"]
            or link["media_type"] != resource.payload["media_type"]
        ):
            raise Conflict("资源类型或文件与文档输入不一致")
        used.add(usage)
    if used != set(row.inputs):
        raise Conflict("文档存在未声明用途的媒体依赖")
    return frozen, row.payload["aspect_ratio"]


def sequence_view(db, work_id, document_id, revision):
    doc, row = read_revision(db, work_id, document_id, revision)
    return {
        "id": str(doc.id),
        "title": doc.title,
        "kind": doc.kind,
        "source_timeline_id": str(doc.source_timeline_id) if doc.source_timeline_id else None,
        "revision": row.revision,
        "schema_version": row.schema_version,
        "adapter_available": document_types.available(doc.kind, row.schema_version),
        "content_hash": row.content_hash,
        "inputs": row.inputs,
        "payload": row.payload,
        "created_at": row.created_at,
    }


def index(db, work_id):
    from app.models.work import WorkTimeline

    result = []
    for doc in db.scalars(
        select(Document).where(Document.work_id == work_id).order_by(Document.created_at)
    ):
        revisions = list(
            db.scalars(
                select(Revision)
                .where(Revision.document_id == doc.id)
                .order_by(Revision.revision.desc())
            )
        )
        link = (
            db.get(WorkTimeline, (work_id, doc.source_timeline_id))
            if doc.source_timeline_id
            else None
        )
        result.append(
            {
                "working_revision": link.document_revision if link else None,
                "id": str(doc.id),
                "title": doc.title,
                "kind": doc.kind,
                "source_timeline_id": str(doc.source_timeline_id)
                if doc.source_timeline_id
                else None,
                "revisions": [
                    {
                        "revision": r.revision,
                        "schema_version": r.schema_version,
                        "adapter_available": document_types.available(doc.kind, r.schema_version),
                        "content_hash": r.content_hash,
                        "created_at": r.created_at,
                    }
                    for r in revisions
                ],
            }
        )
    return result


def sync_working(db, ctx, work_id, timeline_id):
    """Persist an editable revision without promoting it to adopted content."""
    from app.models.work import WorkTimeline
    from app.modules.engineering import schemas, service, timeline
    from app.modules.generation.jobs import document_render_snapshot
    from app.modules.timeline import editing
    from app.modules.timeline.schemas import TimelineDocumentIn

    service.work(db, ctx, work_id, lock=True)
    document = editing.document(db, ctx.project.id, timeline_id)
    if document["items"]:
        document, frozen = document_render_snapshot(
            db, ctx, timeline_id, TimelineDocumentIn.model_validate(document)
        )
    else:
        if document["audio"] or document["visuals"] or document["subtitles"]:
            raise CapabilityUnsupported("当前序列适配器尚不支持没有画面的音频或字幕工程")
        frozen = {"clips": [], "tracks": {"audio": [], "visuals": [], "subtitles": []}}
    snapshot = {"resources": []}
    timeline.store(snapshot, timeline_id, document, frozen)
    for item in sorted(snapshot["resources"], key=lambda i: service.source_order(db, i)):
        src = service.pin(
            db,
            ctx,
            work_id,
            schemas.Add(
                operation="resource.add",
                **{k: item[k] for k in ("usage_id", "source_kind", "source_id", "name")},
            ),
        )
        if src.blob_hash != item["blob_hash"]:
            raise Conflict("工作文档媒体源发生变化")
        item.update(resource_id=str(src.id), blob_hash=src.blob_hash, media_type=src.media_type)
    # Keep output profile from the prior working revision when saving the editor.
    link = db.get(WorkTimeline, (work_id, timeline_id))
    aspects = None
    if link and link.document_revision:
        _, prior = read_revision(
            db, work_id, uuid.uuid5(work_id, "sequence:" + str(timeline_id)), link.document_revision
        )
        aspects = {str(timeline_id): prior.payload["aspect_ratio"]}
    refs = store_snapshot(db, ctx, work_id, snapshot, aspects)
    if not link:
        link = WorkTimeline(work_id=work_id, timeline_id=timeline_id)
        db.add(link)
    link.document_revision = refs[str(timeline_id)]["revision"]
    db.flush()
    return refs[str(timeline_id)]


def sync_linked(db, ctx, timeline_id):
    from app.models.work import WorkTimeline

    work_ids = list(
        db.scalars(
            select(WorkTimeline.work_id)
            .where(WorkTimeline.timeline_id == timeline_id)
            .order_by(WorkTimeline.work_id)
        )
    )
    for work_id in work_ids:
        sync_working(db, ctx, work_id, timeline_id)


def initialize(db, ctx, work_id):
    return append(
        db,
        ctx,
        work_id,
        uuid.uuid5(work_id, "manifest"),
        "work.manifest",
        "作品工程清单",
        {},
        {"format": 1, "outputs": {}},
    )


def closure(db, work_id, reference):
    """Resolve a fixed dependency closure; metadata only, no untrusted file paths."""
    result, active, seen = [], set(), set()

    def visit(ref):
        key = (ref["id"], ref["revision"])
        if key in active:
            raise Conflict("工程依赖存在循环")
        if key in seen:
            return
        if len(seen) >= 2000 or len(active) >= 32:
            raise CapabilityUnsupported("工程依赖超过当前读取上限")
        active.add(key)
        doc, row = read_revision(db, work_id, uuid.UUID(ref["id"]), ref["revision"])
        for link in row.inputs.values():
            child, _ = read_revision(
                db, work_id, uuid.UUID(link["target"]["id"]), link["target"]["revision"]
            )
            if link.get("accepts") and child.kind != link["accepts"]:
                raise Conflict("工程依赖类型不匹配")
            if child.kind == "resource" and doc.kind in {
                "image.document",
                "audio.document",
                "composition.document",
            }:
                _, resource = read_revision(db, work_id, child.id, link["target"]["revision"])
                allowed = {
                    "image.document": {"image"},
                    "audio.document": {"audio", "video"},
                    "composition.document": {"image", "video"},
                }[doc.kind]
                if resource.payload["media_type"] not in allowed:
                    raise Conflict("媒体流类型不适用于此文档")
            visit(link["target"])
        active.remove(key)
        seen.add(key)
        result.append(sequence_view(db, work_id, doc.id, row.revision))

    visit(reference)
    return result
