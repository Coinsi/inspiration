"""Bind input files to a message once; execution always uses immutable references."""

import copy

from app.core.errors import Conflict
from app.modules.attachments import service as attachments
from app.modules.generation.references import Reference, resolve


def request_context(data):
    return {
        "attachment_requests": [a.model_dump(mode="json") for a in data.attachments],
        "reference_requests": [r.model_dump(mode="json") for r in data.references],
    }


def bind_inputs(db, ctx, conversation, message, data):
    local = [Reference(attachment_id=a.attachment_id, role=a.role) for a in data.attachments]
    existing = data.media.references if data.media else data.references
    references, seen = [], {}
    for ref in [*existing, *local]:
        key = (ref.source_type, ref.source_id)
        if key in seen:
            if seen[key] != ref.role:
                raise Conflict("同一素材不能同时作为不同参考角色，请保留一个用途")
            continue
        seen[key] = ref.role
        references.append(ref)
    if len(references) > 8:
        raise Conflict("每条消息最多添加 8 项参考内容")
    attachment_ids = [r.attachment_id for r in references if r.attachment_id]
    bound = attachments.bind(db, ctx, attachment_ids, conversation.id, message.id)
    by_id = {str(a["id"]): a for a in bound}
    current, local_descriptors, existing_descriptors = [], [], []
    local_ids = {a.attachment_id for a in data.attachments}
    for ref in references:
        item = resolve(db, ctx.project.id, ref, user_id=ctx.user.id, allow_audio=not data.media)
        descriptor = {
            **{
                k: v
                for k, v in by_id.get(str(ref.source_id), {}).items()
                if k
                in {
                    "filename",
                    "duration_ms",
                    "width",
                    "height",
                    "size_bytes",
                    "status",
                    "conversation_id",
                    "message_id",
                    "saved_at",
                }
            },
            **item,
            "id": str(item["id"]),
            "role": ref.role,
            "reference": {
                "kind": ref.source_type,
                "id": str(ref.source_id),
                "expected_blob_hash": item["blob_hash"],
                "purpose": ref.role,
            },
        }
        current.append(descriptor)
        if ref.attachment_id in local_ids:
            local_descriptors.append(descriptor)
        else:
            existing_descriptors.append(descriptor)
    execution_data = data
    if data.media:
        execution_data = data.model_copy(
            update={"media": data.media.model_copy(update={"references": references})}
        )
    return (
        execution_data,
        current,
        {
            **request_context(data),
            "attachments": local_descriptors,
            "references": existing_descriptors,
            "media": data.media.model_dump(mode="json") if data.media else None,
        },
    )


def context_references(items):
    return [
        {
            "kind": item["source_type"],
            "id": str(item["id"]),
            "expected_blob_hash": item["blob_hash"],
            "purpose": item.get("role", "reference"),
            "required": True,
        }
        for item in items
    ]


def merge_run_inputs(db, ctx, run, history):
    """A steering turn can introduce files without losing the earlier fixed inputs."""
    from app.modules.harness.context import resolve_reference

    current = history.get("current_attachments", [])
    refs = [resolve_reference(db, ctx, ref)["reference"] for ref in context_references(current)]
    seen = {(r["kind"], str(r["id"])) for r in refs}
    # Accepted tasks keep their own pinned contracts; historical chat media
    # should not exhaust the mandatory media budget on every steering turn.
    refs.extend(
        {**r, "required": r.get("required", False) if r["kind"] == "document" else False}
        for r in run.context.get("references", [])
        if (r["kind"], str(r["id"])) not in seen
    )
    omitted = [{"id": r["id"], "reason": "conversation_reference_window"} for r in refs[64:]]
    run.context = {
        **run.context,
        "references": refs[:64],
        "current_attachments": copy.deepcopy(current),
        "history_reference_omissions": [
            *run.context.get("history_reference_omissions", []),
            *omitted,
        ][-128:],
    }
