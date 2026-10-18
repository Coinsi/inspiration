"""Read-only stable instance time lookup shared by API and engineering tools."""

from app.core.errors import CapabilityUnsupported, NotFound
from app.modules.engineering import documents, native_v2
from app.modules.engineering.native import Time
from app.modules.engineering.timing import encoded, inverse, source_at, value


def locate(db, work_id, query):
    doc, revision = documents.read_revision(db, work_id, query.document_id, query.revision)
    if doc.kind not in native_v2.MODELS or revision.schema_version not in (1, 2):
        raise CapabilityUnsupported("该文档格式没有可解析的时间映射")
    payload = revision.payload
    if revision.schema_version == 1:
        _, payload = native_v2.migrate(doc.kind, revision.inputs, payload)
    model = native_v2.MODELS[doc.kind].model_validate(payload)
    entries = (
        [clip for track in model.tracks for clip in track.clips]
        if doc.kind == "audio.document"
        else model.layers
    )
    item = next((item for item in entries if item.id == query.instance_id), None)
    if item is None:
        raise NotFound("指定使用实例不存在")
    mapping, time = item.source_map, value(query.time)
    placement = value(item.start) if doc.kind == "audio.document" else value(item.active.start)
    if query.direction == "to_source":
        try:
            source = source_at(mapping, time - placement)
        except ValueError:
            matches = []
        else:
            segment = next(
                s
                for s in mapping.segments
                if value(s.local.start) <= time - placement < value(s.local.end)
            )
            matches = [{"segment_id": str(segment.id), "source": encoded(source)}]
    else:
        matches = inverse(mapping, time)
        for match in matches:
            if "point" in match:
                match["point"] = encoded(value(Time.model_validate(match["point"])) + placement)
            else:
                match["range"] = {
                    key: encoded(value(Time.model_validate(t)) + placement)
                    for key, t in match["range"].items()
                }
    return {
        "document_id": str(doc.id),
        "revision": revision.revision,
        "instance_id": str(item.id),
        "source_revision": revision.inputs[item.source]["target"],
        "direction": query.direction,
        "space": "source" if query.direction == "to_source" else "document",
        "status": "outside_usage"
        if not matches
        else "ambiguous_occurrence"
        if len(matches) > 1 or "range" in matches[0]
        else "located",
        "matches": matches,
    }
