"""Fixed timeline snapshots; reviewed changes use the existing editor as sole writer."""

import uuid

from app.core.errors import Conflict
from app.modules.generation.jobs import document_render_snapshot
from app.modules.timeline import editing
from app.modules.timeline.schemas import TimelineDocumentIn


def capture(db, ctx, candidate, command):
    timeline = editing.lock(db, ctx.project.id, command.timeline_id)
    if timeline.revision != command.timeline_revision:
        raise Conflict("时间线已更新，请重新载入后捕获")
    document, frozen = document_render_snapshot(
        db,
        ctx,
        timeline.id,
        TimelineDocumentIn.model_validate(editing.document(db, ctx.project.id, timeline.id)),
    )
    store(candidate, timeline.id, document, frozen)


def store(candidate, timeline_id, document, frozen):
    tid = str(timeline_id)
    resources = [r for r in candidate["resources"] if r.get("timeline_capture") != tid]

    def reference(part, source, name, blob_hash):
        usage = str(uuid.uuid5(timeline_id, part))
        resources.append(
            {
                "usage_id": usage,
                "source_kind": "generation",
                "source_id": source,
                "name": name,
                "timeline_capture": tid,
                "blob_hash": blob_hash,
            }
        )
        return usage

    for index, (clip, item) in enumerate(zip(frozen["clips"], document["items"], strict=True)):
        clip["usage_id"] = reference(
            f"clip:{item['id']}", clip["generation_id"], f"片段 {index + 1}", clip["blob_hash"]
        )
        # Resolve legacy selected-generation fallback in the editable source snapshot too.
        document["items"][index]["generation_id"] = clip["generation_id"]
    for index, audio in enumerate(frozen["tracks"]["audio"]):
        audio["usage_id"] = reference(
            f"audio:{audio['id']}",
            audio["generation_id"],
            audio.get("name") or f"音轨 {index + 1}",
            audio["blob_hash"],
        )
    for visual in frozen["tracks"]["visuals"]:
        visual["usage_id"] = reference(
            f"visual:{visual['id']}",
            visual["generation_id"],
            visual.get("name") or "叠加画面",
            visual["blob_hash"],
        )
    candidate["resources"] = resources
    candidate["schema"] = "studio.timeline_capture@1"
    captures = candidate.setdefault("timeline_captures", {})
    captures[tid] = {
        "source_timeline_id": tid,
        "source_revision": document["revision"],
        "document": document,
        "render_snapshot": frozen,
        "read_only": True,
    }
