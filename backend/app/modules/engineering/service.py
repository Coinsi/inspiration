"""Append-only work snapshots and reviewed changes through existing domain writers.

Timeline snapshots are frozen records; editing.save_document remains the sole writer.
This bridge does not implement the full future composition format.
"""

import copy
import hashlib
import json
import uuid

from sqlalchemy import select

from app.core import audit
from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.core.permissions import require
from app.models.engineering import EngineeringChange, EngineeringCommit, EngineeringResource
from app.models.generation import Generation
from app.models.storage import Blob
from app.models.work import CreativeWork
from app.modules.conversation.works import get as get_work
from app.modules.engineering import schemas


def work(db, ctx, work_id, lock=False):
    if lock:
        # Timeline editing also locks project first. Keep one order across entry points.
        from app.models.identity import Project

        db.scalar(select(Project).where(Project.id == ctx.project.id).with_for_update())
    row = (
        db.scalar(
            select(CreativeWork)
            .where(CreativeWork.id == work_id, CreativeWork.project_id == ctx.project.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if lock
        else get_work(db, ctx.project.id, work_id)
    )
    if not row:
        raise NotFound("作品不存在")
    get_work(db, ctx.project.id, work_id)
    return row


def commit_at(db, work_id, revision):
    row = db.scalar(
        select(EngineeringCommit).where(
            EngineeringCommit.work_id == work_id, EngineeringCommit.revision == revision
        )
    )
    if not row:
        raise NotFound("工程版本不存在")
    return row


def snapshot_at(db, work_id, revision):
    if revision == 0:
        from app.models.engineering import EngineeringDocumentRevision

        initial = db.get(EngineeringDocumentRevision, (uuid.uuid5(work_id, "manifest"), 1))
        snapshot = {"schema": "studio.resource_manifest@1", "resources": []}
        if initial and not initial.inputs:
            snapshot.update(
                document_format=1,
                document_roots={},
                resource_roots={},
                manifest_ref={"id": str(initial.document_id), "revision": 1},
            )
        return snapshot
    return copy.deepcopy(commit_at(db, work_id, revision).snapshot)


def read(db, ctx, work_id):
    row = work(db, ctx, work_id)
    return {
        "work_id": row.id,
        "revision": row.engineering_revision,
        "snapshot": snapshot_at(db, row.id, row.engineering_revision),
        "capabilities": [
            "resource.add",
            "resource.remove",
            "restore",
            "timeline.capture",
            "timeline.edit",
            "timeline.restore",
            "candidate.preview",
            "document.put",
            "document.migrate",
            "document.retarget",
            "instance.retarget",
            "instance.retarget_path",
            "time.locate",
            "package.import",
            "package.export",
            "change.impact",
        ],
        "renderable": False,
    }


def pin(db, ctx, work_id, command):
    # Lock library identity using the same lock as trash; pins and trash serialize.
    if command.source_kind == "library":
        from app.modules.library.service import version

        _, src = version(db, ctx.project.id, command.source_id, lock=True)
        if src.status != "ready" or not src.original_hash:
            raise CapabilityUnsupported("素材尚未准备好")
        blob_hash, media_type = src.original_hash, src.media_type
    else:
        src = db.get(Generation, command.source_id)
        if not src or src.project_id != ctx.project.id:
            raise NotFound("生成结果不存在")
        blob_hash, media_type = src.output_blob_hash, src.output_type
    if not blob_hash or not db.get(Blob, blob_hash):
        raise CapabilityUnsupported("源文件不可用")
    row = db.scalar(
        select(EngineeringResource).where(
            EngineeringResource.work_id == work_id,
            EngineeringResource.source_kind == command.source_kind,
            EngineeringResource.source_id == command.source_id,
        )
    )
    if row:
        if row.blob_hash != blob_hash or row.media_type != media_type:
            raise Conflict("源版本内容已发生变化，不能覆盖固定引用")
        return row
    row = EngineeringResource(
        work_id=work_id,
        source_kind=command.source_kind,
        source_id=command.source_id,
        blob_hash=blob_hash,
        media_type=media_type,
        media_version_id=command.source_id if command.source_kind == "library" else None,
        generation_id=command.source_id if command.source_kind == "generation" else None,
    )
    db.add(row)
    db.flush()
    return row


def source_order(db, item):
    from app.models.library import MediaVersion

    src = (
        db.get(MediaVersion, uuid.UUID(item["source_id"]))
        if item["source_kind"] == "library"
        else None
    )
    return (item["source_kind"], str(src.media_id) if src else item["source_id"], item["source_id"])


def validate_sources(db, ctx, work_id, snapshot):
    # Recheck ownership/readiness at adoption, including restored historical sources.
    items = list(snapshot["resources"])
    if snapshot.get("manifest_ref"):
        from app.modules.engineering.documents import closure

        # A nested document can retain a resource no longer listed as a top-level
        # usage. Validate every reachable leaf, not only the visible material list.
        for node in closure(db, work_id, snapshot["manifest_ref"]):
            if node["kind"] == "resource":
                items.append({**node["payload"], "usage_id": node["id"], "name": node["title"]})
    checked = set()
    for item in sorted(items, key=lambda x: source_order(db, x)):
        key = (item["resource_id"], item["blob_hash"])
        if key in checked:
            continue
        row = pin(
            db,
            ctx,
            work_id,
            schemas.Add(
                operation="resource.add",
                usage_id=item["usage_id"],
                source_kind=item["source_kind"],
                source_id=item["source_id"],
                name=item["name"],
            ),
        )
        if str(row.id) != item["resource_id"] or row.blob_hash != item["blob_hash"]:
            raise Conflict("工程引用与固定源不一致")
        checked.add(key)


def change_out(row):
    return {
        "id": row.id,
        "created_at": row.created_at,
        "base_revision": row.base_revision,
        "fingerprint": row.fingerprint,
        "status": row.status,
        "committed_revision": row.committed_revision,
        "snapshot": row.snapshot,
        "timeline_update": row.timeline_update,
        "preview_job_id": row.preview_job_id,
        "canonical_commands": row.canonical_commands,
        "read_preconditions": row.read_preconditions,
        "write_scopes": row.write_scopes,
        "provenance": row.provenance,
        "rebased_from_id": row.rebased_from_id,
    }


def propose(db, ctx, work_id, data):
    require(ctx.role, "engineering.edit")
    row = work(db, ctx, work_id, lock=True)
    # Keep fingerprints of requests made before the collaboration contract stable.
    request_content = data.model_dump(mode="json", exclude={"read_preconditions", "provenance"})
    if data.read_preconditions:
        request_content["read_preconditions"] = [
            item.model_dump(mode="json") for item in data.read_preconditions
        ]
    if any(data.provenance.model_dump().values()):
        request_content["provenance"] = data.provenance.model_dump(mode="json")
    fp = hashlib.sha256(
        json.dumps(
            request_content, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    prior = db.scalar(
        select(EngineeringChange).where(
            EngineeringChange.work_id == row.id,
            EngineeringChange.created_by == ctx.user.id,
            EngineeringChange.request_key == data.request_key,
        )
    )
    if prior:
        if prior.fingerprint != fp:
            raise Conflict("请求标识已用于不同修改")
        return change_out(prior)
    if data.base_revision > row.engineering_revision:
        raise Conflict("工程基线版本尚不存在")
    if any(isinstance(c, schemas.Restore) for c in data.commands) and len(data.commands) != 1:
        raise CapabilityUnsupported("恢复操作须单独审阅")
    base = snapshot_at(db, row.id, data.base_revision)
    candidate = copy.deepcopy(base)
    imports = [c for c in data.commands if isinstance(c, schemas.ImportPackage)]
    if imports:
        if len(data.commands) != 1 or row.engineering_revision != 0:
            raise Conflict("工程包须单独导入尚未采用内容的新作品")
        from app.models.engineering import EngineeringDocument

        if db.scalar(
            select(EngineeringDocument.id)
            .where(
                EngineeringDocument.work_id == row.id,
                EngineeringDocument.kind != "work.manifest",
            )
            .limit(1)
        ):
            raise Conflict("作品已有工作文档，请新建作品后导入")
    update = {}
    timeline_updates = []
    editing_commands = [
        c
        for c in data.commands
        if isinstance(c, (schemas.EditTimeline, schemas.RestoreTimeline, schemas.EditDocument))
    ]
    if editing_commands and any(isinstance(c, schemas.CaptureTimeline) for c in data.commands):
        raise CapabilityUnsupported("剪辑修改不能与重新捕获混用，请保留明确的编辑基线")
    native_commands = []
    for c in data.commands:
        if isinstance(c, schemas.RetargetInstancePath):
            from app.modules.engineering.instances import retarget_path

            edits, forks = retarget_path(db, row.id, c)
            native_commands.extend(edits)
            candidate.setdefault("derived_documents", []).extend(forks)
            continue
        if isinstance(c, schemas.RetargetInstances):
            from app.modules.engineering.instances import retarget

            native_commands.append(retarget(db, row.id, c))
            continue
        if isinstance(c, schemas.RetargetDocument):
            from app.modules.engineering import documents

            doc, fixed = documents.read_revision(db, row.id, c.document_id, c.expected_revision)
            inputs = copy.deepcopy(fixed.inputs)
            aliases = set()
            for replacement in c.inputs:
                if replacement.alias in aliases:
                    raise Conflict("一次变更不能重复替换同一输入")
                aliases.add(replacement.alias)
                link = inputs.get(replacement.alias)
                if not link or link.get("target") != replacement.expected.model_dump(mode="json"):
                    raise Conflict("使用位置的原引用已变化")
                link["target"] = replacement.target.model_dump(mode="json")
            native_commands.append(
                schemas.PutNativeDocument(
                    operation="document.put",
                    document_id=doc.id,
                    expected_revision=c.expected_revision,
                    kind=doc.kind,
                    schema_version=fixed.schema_version,
                    title=doc.title,
                    inputs=inputs,
                    payload=copy.deepcopy(fixed.payload),
                )
            )
            continue
        if isinstance(c, schemas.MigrateDocument):
            from app.modules.engineering import document_types, documents

            doc, fixed = documents.read_revision(db, row.id, c.document_id, c.document_revision)
            if not document_types.adapter(doc.kind, c.target_schema_version).editable:
                raise CapabilityUnsupported("该类型需要专用迁移适配器")
            inputs, payload, steps = document_types.migrate(
                doc.kind, fixed.schema_version, c.target_schema_version, fixed.inputs, fixed.payload
            )
            native_commands.append(
                schemas.PutNativeDocument(
                    operation="document.put",
                    document_id=doc.id,
                    expected_revision=c.expected_revision,
                    kind=doc.kind,
                    schema_version=c.target_schema_version,
                    title=doc.title,
                    inputs=inputs,
                    payload=payload,
                )
            )
            candidate.setdefault("migrations", []).append(
                {
                    "document_id": str(doc.id),
                    "source_revision": fixed.revision,
                    "steps": steps,
                }
            )
            continue
        if isinstance(c, schemas.ImportPackage):
            from app.modules.engineering.packages import import_snapshot

            candidate = import_snapshot(db, ctx, row.id, c.package)
            continue
        if isinstance(c, schemas.PutNativeDocument):
            # Resolve the entire batch after resources have been pinned.
            native_commands.append(c)
            continue
        if isinstance(c, (schemas.EditTimeline, schemas.RestoreTimeline, schemas.EditDocument)):
            from app.modules.engineering.review import propose_timeline

            if isinstance(c, schemas.EditDocument):
                from app.modules.engineering.documents import read_revision

                doc, fixed = read_revision(db, row.id, c.document_id, c.document_revision)
                if doc.kind != "sequence.timeline" or not doc.source_timeline_id:
                    raise CapabilityUnsupported("该文档尚未接入编辑器")
                editor = (
                    c.document.model_dump(mode="json")
                    if c.document
                    else copy.deepcopy(fixed.payload["editor"])
                )
                if c.document and c.document.revision != c.timeline_revision:
                    raise Conflict("时间线版本前提不一致")
                editor["revision"] = c.timeline_revision
                c = schemas.EditTimeline(
                    operation="timeline.edit",
                    timeline_id=doc.source_timeline_id,
                    document=editor,
                    aspect_ratio=c.aspect_ratio or fixed.payload["aspect_ratio"],
                )
            update = propose_timeline(db, ctx, row, candidate, c)
            if any(u["timeline_id"] == update["timeline_id"] for u in timeline_updates):
                raise Conflict("一次工程变更不能重复修改同一条时间线")
            timeline_updates.append(update)
            continue
        if isinstance(c, schemas.CaptureTimeline):
            from app.modules.engineering.timeline import capture

            capture(db, ctx, candidate, c)
            continue
        if isinstance(c, schemas.Restore):
            candidate = snapshot_at(db, row.id, c.revision)
            continue
        usage = str(c.usage_id)
        found = next((i for i in candidate["resources"] if i["usage_id"] == usage), None)
        if isinstance(c, schemas.Remove):
            if not found:
                raise NotFound("作品中的素材使用不存在")
            if found.get("timeline_capture"):
                raise Conflict("时间线快照中的素材不能单独移除，请重新捕获时间线")
            candidate["resources"].remove(found)
        else:
            if found:
                raise Conflict("使用标识已存在")
            candidate["resources"].append(
                {
                    "usage_id": usage,
                    "source_kind": c.source_kind,
                    "source_id": str(c.source_id),
                    "name": c.name,
                }
            )
    if len(candidate["resources"]) > 1000:
        raise CapabilityUnsupported("本阶段单个工程最多保存1000个素材使用")
    if len({i["usage_id"] for i in candidate["resources"]}) != len(candidate["resources"]):
        raise Conflict("素材使用标识重复")
    for item in sorted(candidate["resources"], key=lambda i: source_order(db, i)):
        src = pin(
            db,
            ctx,
            row.id,
            schemas.Add(
                operation="resource.add",
                **{k: item[k] for k in ("usage_id", "source_kind", "source_id", "name")},
            ),
        )
        if "resource_id" in item and (
            item["resource_id"] != str(src.id) or item["blob_hash"] != src.blob_hash
        ):
            raise Conflict("工程引用与固定源不一致")
        if "blob_hash" in item and item["blob_hash"] != src.blob_hash:
            raise Conflict("时间线引用与固定源不一致")
        item.update(resource_id=str(src.id), blob_hash=src.blob_hash, media_type=src.media_type)
    from app.modules.engineering import batch
    from app.modules.engineering.documents import closure, store_snapshot

    batch.apply(
        db,
        ctx,
        row.id,
        candidate,
        native_commands,
    )

    roots = store_snapshot(
        db,
        ctx,
        row.id,
        candidate,
        {u["timeline_id"]: u["aspect_ratio"] for u in timeline_updates} or None,
    )
    reachable = closure(db, row.id, candidate["manifest_ref"])
    reachable_ids = {node["id"] for node in reachable}
    if any(
        not c.publish_output and str(c.document_id) not in reachable_ids for c in native_commands
    ):
        raise Conflict("依赖文档必须被作品输出引用，不能创建不可达的隐藏文档")
    validate_sources(db, ctx, row.id, candidate)
    from app.modules.engineering.native_v2 import review_items

    candidate["sync_reviews"] = review_items(reachable)
    for item in timeline_updates:
        item["document_ref"] = roots[item["timeline_id"]]
    if timeline_updates:
        update = timeline_updates[0]
        if len(timeline_updates) > 1:
            update["additional_updates"] = timeline_updates[1:]
            update["summary"].append(
                f"本候选共修改 {len(timeline_updates)} 条剪辑，全部预览完成后一起采用"
            )
        if native_commands:
            update["summary"].extend(
                f"同时采用工程文档：{command.title}（{command.kind}）；剪辑预览不代表该文档已完成渲染验收"
                for command in native_commands
            )
    from app.modules.engineering.collaboration import capture_contract

    reads, writes = capture_contract(db, ctx, row.id, base, data, timeline_updates)
    change = EngineeringChange(
        work_id=row.id,
        created_by=ctx.user.id,
        request_key=data.request_key,
        fingerprint=fp,
        base_revision=data.base_revision,
        snapshot=candidate,
        timeline_update=update,
        canonical_commands=[command.model_dump(mode="json") for command in data.commands],
        read_preconditions=reads,
        write_scopes=writes,
        provenance=data.provenance.model_dump(mode="json", exclude_none=True),
    )
    db.add(change)
    db.flush()
    return change_out(change)


def accept(db, ctx, work_id, change_id, data):
    require(ctx.role, "engineering.adopt")
    row = work(db, ctx, work_id, lock=True)
    change = db.scalar(
        select(EngineeringChange).where(
            EngineeringChange.id == change_id, EngineeringChange.work_id == row.id
        )
    )
    if not change:
        raise NotFound("修改候选不存在")
    if change.fingerprint != data.fingerprint:
        raise Conflict("审阅内容不匹配")
    if change.status == "accepted":
        return change_out(change)
    if change.base_revision != row.engineering_revision:
        raise Conflict("工程已更新，旧候选保留但不能直接采用")
    from app.modules.engineering.collaboration import conflicts

    changed = conflicts(db, ctx, row, change, snapshot_at(db, row.id, row.engineering_revision))
    if changed:
        raise Conflict("候选的读取或写入前提已变化，请重新检查后再采用", {"conflicts": changed})
    validate_sources(db, ctx, row.id, change.snapshot)
    from app.modules.engineering.documents import closure

    if change.snapshot.get("manifest_ref"):
        from app.modules.engineering.native_v2 import review_items

        issues = review_items(closure(db, row.id, change.snapshot["manifest_ref"]))
        required = {item["key"] for item in issues}
        if required - set(data.acknowledged_reviews):
            raise Conflict("存在需要复核的同步变化，请检查并明确确认后再采用")
    committed_snapshot = copy.deepcopy(change.snapshot)
    from app.modules.engineering.review import pin_native_artifacts

    pin_native_artifacts(db, ctx, change, committed_snapshot)
    if change.timeline_update:
        from app.modules.engineering.review import adopt_timeline

        adopt_timeline(db, ctx, change, committed_snapshot)
    revision = row.engineering_revision + 1
    db.add(
        EngineeringCommit(
            work_id=row.id,
            revision=revision,
            parent_revision=row.engineering_revision,
            snapshot=committed_snapshot,
            created_by=ctx.user.id,
        )
    )
    row.engineering_revision = revision
    change.status, change.committed_revision = "accepted", revision
    audit.record(
        db,
        action="engineering.adopt",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=row.id,
        detail={"change_id": str(change.id), "revision": revision},
    )
    db.flush()
    return change_out(change)


def rebase(db, ctx, work_id, change_id, data):
    """Create a new reviewable candidate; never mutate the old one or auto-adopt."""
    require(ctx.role, "engineering.edit")
    row = work(db, ctx, work_id, lock=True)
    from app.modules.engineering import collaboration, review

    change = review.get_change(db, row.id, change_id)
    if change.fingerprint != data.fingerprint:
        raise Conflict("审阅内容不匹配")
    prior = db.scalar(
        select(EngineeringChange).where(
            EngineeringChange.work_id == row.id,
            EngineeringChange.created_by == ctx.user.id,
            EngineeringChange.request_key == data.request_key,
        )
    )
    if prior:
        if prior.rebased_from_id != change.id or prior.base_revision != data.expected_revision:
            raise Conflict("请求标识已用于不同修改")
        return change_out(prior)
    if change.status != "proposed":
        raise Conflict("只允许整合尚未采用的候选")
    if row.engineering_revision != data.expected_revision:
        raise Conflict("工程再次更新，请重新检查整合目标")
    current = snapshot_at(db, row.id, row.engineering_revision)
    commands = collaboration.replay_commands(db, row, change, current)
    changed = collaboration.conflicts(db, ctx, row, change, current)
    if changed:
        raise Conflict("候选依赖或修改范围存在冲突，已保留原候选", {"conflicts": changed})
    declared = [
        schemas.ReadPrecondition(kind=read["kind"], id=read["id"], revision=revision)
        for read in change.read_preconditions
        if "declared" in read["origins"]
        for revision in read["declared_revisions"]
    ]
    result = propose(
        db,
        ctx,
        row.id,
        schemas.Propose(
            request_key=data.request_key,
            base_revision=data.expected_revision,
            commands=commands,
            read_preconditions=declared,
            provenance=schemas.Provenance.model_validate(change.provenance),
        ),
    )
    rebased = db.get(EngineeringChange, result["id"])
    rebased.rebased_from_id = change.id
    audit.record(
        db,
        action="engineering.rebase",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=row.id,
        detail={
            "from_change_id": str(change.id),
            "change_id": str(rebased.id),
            "revision": row.engineering_revision,
        },
    )
    db.flush()
    return change_out(rebased)
