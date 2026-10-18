"""Versioned editable package with explicit external media dependencies.

The reference package is portable as data, not self-contained media. Import
revalidates local source authorization and never fetches arbitrary URLs.
"""

import copy
import json
import uuid

from pydantic import Field

from app.core.errors import CapabilityUnsupported, Conflict
from app.modules.engineering import document_types, documents
from app.modules.engineering.native import Reference, Strict


class Node(Strict):
    id: uuid.UUID
    revision: int = Field(ge=1, strict=True)
    kind: str = Field(min_length=1, max_length=40)
    schema_version: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1, max_length=255)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    inputs: dict
    payload: dict


class Package(Strict):
    format: str
    root: Reference
    documents: list[Node] = Field(min_length=1, max_length=2000)
    includes_media_files: bool
    external_media: list[str] = Field(max_length=2000)


def export(db, work_id, snapshot):
    ref = snapshot.get("manifest_ref")
    if not ref:
        raise Conflict("工程尚未建立可导出的根文档")
    rows = documents.closure(db, work_id, ref)
    return {
        "format": "studio.editable-package@1",
        "root": ref,
        "documents": [{k: r[k] for k in Node.model_fields} for r in rows],
        "includes_media_files": False,
        "external_media": sorted(
            {r["payload"]["blob_hash"] for r in rows if r["kind"] == "resource"}
        ),
    }


def inspect(raw):
    try:
        if len(json.dumps(raw, allow_nan=False).encode()) > 16 * 1024 * 1024:
            raise ValueError("工程包描述超过 16 MiB")
        package = Package.model_validate(raw)
        if package.format != "studio.editable-package@1" or package.includes_media_files:
            raise ValueError("当前接口只接受版本 1 的引用工程包")
        nodes = {}
        for node in package.documents:
            key = (str(node.id), node.revision)
            if key in nodes:
                raise ValueError("重复文档修订")
            if documents.digest(node.inputs, node.payload) != node.content_hash:
                raise ValueError("文档内容校验和不匹配")
            document_types.validate_stored(
                node.kind, node.schema_version, node.inputs, node.payload
            )
            nodes[key] = node
        order, active, seen = [], set(), set()

        def visit(ref):
            parsed = Reference.model_validate(ref)
            key = (str(parsed.id), parsed.revision)
            if key in active or len(active) >= 32:
                raise ValueError("循环引用或依赖深度超过限制")
            if key in seen:
                return
            if key not in nodes:
                raise ValueError("工程包缺少文档依赖")
            active.add(key)
            node = nodes[key]
            for link in node.inputs.values():
                visit(link["target"])
                target = Reference.model_validate(link["target"])
                child = nodes[(str(target.id), target.revision)]
                if link.get("accepts") and link["accepts"] != child.kind:
                    raise ValueError("依赖类型不匹配")
            active.remove(key)
            seen.add(key)
            order.append(node)

        visit(package.root.model_dump(mode="json"))
        if len(seen) != len(nodes) or order[-1].kind != "work.manifest":
            raise ValueError("工程包包含根以外的对象或根类型错误")
        media = {n.payload["blob_hash"] for n in order if n.kind == "resource"}
        if set(package.external_media) != media or len(package.external_media) != len(media):
            raise ValueError("外部媒体清单不完整或重复")
        return package, order
    except (ValueError, KeyError, TypeError) as exc:
        raise CapabilityUnsupported("工程包校验失败", {"reason": str(exc)}) from exc


def import_snapshot(db, ctx, work_id, raw):
    from app.core.permissions import require
    from app.models.timeline import Timeline
    from app.models.work import WorkTimeline
    from app.modules.engineering import schemas, service, timeline
    from app.modules.generation.jobs import document_render_snapshot
    from app.modules.timeline import editing
    from app.modules.timeline.schemas import TimelineDocumentIn

    _, order = inspect(raw)
    snapshot = {"schema": "studio.resource_manifest@1", "resources": [], "native_roots": {}}
    mapping = {}
    identities = {str(n.id): uuid.uuid4() for n in order}
    # Each exact resource is authorized and pinned before writing any editing projection.
    for node in order:
        if node.kind != "resource":
            continue
        p = node.payload
        command = schemas.Add(
            operation="resource.add",
            usage_id=uuid.uuid4(),
            source_kind=p["source_kind"],
            source_id=p["source_id"],
            name=node.title,
        )
        pinned = service.pin(db, ctx, work_id, command)
        if pinned.blob_hash != p["blob_hash"] or pinned.media_type != p["media_type"]:
            raise Conflict("本地素材与工程包所需内容不一致")
        payload = {**p, "resource_id": str(pinned.id)}
        mapping[(str(node.id), node.revision)] = documents.append(
            db, ctx, work_id, identities[str(node.id)], node.kind, node.title, {}, payload
        )
        snapshot["resources"].append(
            {
                "usage_id": str(command.usage_id),
                "source_kind": p["source_kind"],
                "source_id": p["source_id"],
                "name": node.title,
                "resource_id": str(pinned.id),
                "blob_hash": pinned.blob_hash,
                "media_type": pinned.media_type,
            }
        )
    for node in order:
        key = (str(node.id), node.revision)
        if node.kind in {"resource", "work.manifest"}:
            continue
        if node.kind == "sequence.timeline":
            require(ctx.role, "timeline.edit")
            t = Timeline(
                project_id=ctx.project.id, name=node.title, kind="main", created_by=ctx.user.id
            )
            db.add(t)
            db.flush()
            editor = copy.deepcopy(node.payload["editor"])
            for collection in editor.values():
                for item in collection:
                    item["id"] = str(uuid.uuid4())
            dto = TimelineDocumentIn.model_validate({**editor, "revision": 0})
            editing.save_document(db, ctx, t.id, dto)
            current = TimelineDocumentIn.model_validate(editing.document(db, ctx.project.id, t.id))
            if current.items:
                normalized, frozen = document_render_snapshot(db, ctx, t.id, current)
            else:
                normalized = current.model_dump(mode="json")
                frozen = {"clips": [], "tracks": {"audio": [], "visuals": [], "subtitles": []}}
                if current.audio or current.visuals or current.subtitles:
                    raise CapabilityUnsupported("空画面剪辑的轨道尚不可迁移")
            timeline.store(snapshot, t.id, normalized, frozen)
            for item in snapshot["resources"]:
                if "resource_id" not in item:
                    pinned = service.pin(
                        db,
                        ctx,
                        work_id,
                        schemas.Add(
                            operation="resource.add",
                            **{
                                k: item[k] for k in ("usage_id", "source_kind", "source_id", "name")
                            },
                        ),
                    )
                    item.update(resource_id=str(pinned.id), media_type=pinned.media_type)
            refs = documents.store_snapshot(
                db, ctx, work_id, snapshot, {str(t.id): node.payload["aspect_ratio"]}
            )
            mapping[key] = refs[str(t.id)]
            db.add(
                WorkTimeline(
                    work_id=work_id, timeline_id=t.id, document_revision=mapping[key]["revision"]
                )
            )
            continue
        links = copy.deepcopy(node.inputs)
        for link in links.values():
            ref = link["target"]
            link["target"] = mapping[(ref["id"], ref["revision"])]
        mapping[key] = documents.append(
            db,
            ctx,
            work_id,
            identities[str(node.id)],
            node.kind,
            node.title,
            links,
            node.payload,
            schema_version=node.schema_version,
            preserve_unknown=True,
        )
        snapshot["native_roots"][str(identities[str(node.id)])] = mapping[key]
    # Only exported roots become outputs; nested documents remain dependencies.
    manifest = order[-1]
    roots = {
        tuple((ref["target"]["id"], ref["target"]["revision"]))
        for ref in manifest.inputs.values()
        if ref.get("accepts") not in {"resource", "sequence.timeline"}
    }
    snapshot["native_roots"] = {str(identities[i]): mapping[(i, rev)] for i, rev in roots}
    snapshot["preserved_output_streams"] = {
        str(identities[manifest.inputs[output["input"]]["target"]["id"]]): output["streams"]
        for output in manifest.payload["outputs"].values()
        if manifest.inputs[output["input"]]["accepts"] not in {"resource", "sequence.timeline"}
    }
    snapshot["import_provenance"] = {
        "format": "studio.editable-package@1",
        "original_root": str(manifest.id),
    }
    return snapshot
