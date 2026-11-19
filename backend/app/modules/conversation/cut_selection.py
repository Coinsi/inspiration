"""Resolve a quoted editorial selection against its saved revision, including older cuts."""

from sqlalchemy import select

from app.core.errors import CapabilityUnsupported, NotFound
from app.models.timeline import TimelineRevision
from app.modules.conversation.works import timeline_ids
from app.modules.media.timing import duration
from app.modules.timeline import editing, service


def resolve(db, ctx, work_id, selection):
    timeline = service._get_timeline(db, ctx.project.id, selection.timeline_id)
    if work_id and timeline.id not in timeline_ids(db, work_id):
        raise CapabilityUnsupported("这段剪辑属于另一部作品，请重新选择时间线")
    if timeline.revision == selection.revision:
        doc = editing.document(db, ctx.project.id, timeline.id)
    else:
        doc = db.scalar(
            select(TimelineRevision.snapshot).where(
                TimelineRevision.timeline_id == timeline.id,
                TimelineRevision.revision == selection.revision,
            )
        )
    if not doc:
        raise NotFound("找不到引用的剪辑版本")
    start, end = selection.start_ms, selection.end_ms
    if end <= start or end > duration(doc["items"]):
        raise CapabilityUnsupported("选区超出了这版时间线")
    offset, clips = 0, []
    for i, item in enumerate(doc["items"]):
        at = offset + item.get("gap_before_ms", 0)
        source = item.get("in_point_ms", 0)
        length = item["out_point_ms"] - source if item.get("out_point_ms") else item["duration_ms"]
        stop = at + length
        offset = (
            stop
            + item.get("gap_after_ms", 0)
            - (item.get("transition") or {}).get("duration_ms", 0)
        )
        if selection.clip_id and not selection.track and item.get("id") != str(selection.clip_id):
            continue
        left, right = max(at, start), min(stop, end)
        if left < right:
            clips.append(
                {
                    "id": item.get("id"),
                    "name": item.get("note") or f"片段 {i + 1}",
                    "source_id": item.get("generation_id"),
                    "start_ms": left,
                    "end_ms": right,
                    "source_start_ms": source + left - at,
                    "source_end_ms": source + right - at,
                }
            )
    if selection.clip_id and not selection.track and not clips:
        raise CapabilityUnsupported("这版时间线的选区中没有该片段")
    result = {**selection.model_dump(mode="json"), "timeline": timeline.name, "clips": clips}
    for track, fields in (
        ("audio", ("name", "generation_id", "gain_db", "muted")),
        ("visuals", ("name", "generation_id", "track", "hidden")),
        ("subtitles", ("text",)),
    ):
        matches = []
        for entry in doc.get(track, []):
            at = entry["start_ms"]
            stop = entry["end_ms"] if track == "subtitles" else at + entry["duration_ms"]
            if max(at, start) < min(stop, end):
                matches.append(
                    {
                        "id": entry.get("id"),
                        "start_ms": max(at, start),
                        "end_ms": min(stop, end),
                        **{k: entry.get(k) for k in fields},
                    }
                )
        if matches:
            result[track] = matches[:24]
            if len(matches) > 24:
                result[f"{track}_total"] = len(matches)
        if selection.track == track:
            focus = next(
                (entry for entry in matches if entry["id"] == str(selection.clip_id)), None
            )
            if not focus:
                raise CapabilityUnsupported("这版时间线的选区中没有指定的轨道片段")
            result["focus"] = {"track": track, **focus}
    return result
