"""Resolve temporary references inside an atomic document proposal.

Only the request may use candidate_document references. Stored revisions always
contain concrete revision numbers. No implicit upgrade of other usages occurs.
"""

import copy

from app.core.errors import Conflict
from app.models.engineering import EngineeringDocument
from app.modules.engineering import document_types, documents


def apply(db, ctx, work_id, snapshot, commands):
    by_id = {str(c.document_id): c for c in commands}
    if len(by_id) != len(commands):
        raise Conflict("一次变更不能重复写同一个文档")
    roots = snapshot.setdefault("native_roots", {})
    resolved, active = {}, set()

    def visit(identity):
        if identity in resolved:
            return resolved[identity]
        if identity in active:
            raise Conflict("本次变更的文档依赖存在循环")
        if identity not in by_id:
            raise Conflict("候选引用没有对应的文档修改")
        active.add(identity)
        command = by_id[identity]
        if not document_types.adapter(command.kind, command.schema_version).editable:
            raise Conflict("此文档类型只能通过对应的领域适配器修改")
        previous = roots.get(identity)
        if not command.publish_output and (
            command.expected_revision or db.get(EngineeringDocument, command.document_id)
        ):
            raise Conflict("依赖文档派生必须使用新身份，不能隐藏或覆盖已有输出")
        if (previous["revision"] if previous else 0) != command.expected_revision:
            raise Conflict("编辑文档前提已变化")
        links = copy.deepcopy(command.inputs)
        for link in links.values():
            target = link.get("target") if isinstance(link, dict) else None
            if isinstance(target, dict) and "candidate_document" in target:
                if set(target) != {"candidate_document"}:
                    raise Conflict("候选引用不能混用固定修订字段")
                link["target"] = visit(target["candidate_document"])
        resolved[identity] = documents.append(
            db,
            ctx,
            work_id,
            command.document_id,
            command.kind,
            command.title,
            links,
            command.payload,
            schema_version=command.schema_version,
        )
        active.remove(identity)
        return resolved[identity]

    for identity in by_id:
        visit(identity)
    roots.update(
        {identity: ref for identity, ref in resolved.items() if by_id[identity].publish_output}
    )
    return resolved
