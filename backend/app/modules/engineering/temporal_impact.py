"""Conservative render-time impact through fixed native dependency instances.

Each side is in its own output's time space. Unknown formats remain unknown;
replacements without proven lineage invalidate the whole selected use, not every
use of a shared resource. This report never mutates or cancels historical jobs.
"""

from fractions import Fraction
from math import ceil

from app.modules.engineering import native_v2
from app.modules.engineering.timing import affected_ranges, encoded, value

LIMIT = 4096


def model(node):
    if node["kind"] not in native_v2.MODELS or node["schema_version"] not in (1, 2):
        return None
    payload = node["payload"]
    if node["schema_version"] == 1:
        _, payload = native_v2.migrate(node["kind"], node["inputs"], payload)
    return native_v2.MODELS[node["kind"]].model_validate(payload)


def entries(doc):
    if isinstance(doc, native_v2.AudioDocument):
        return {
            str(clip.id): (clip, value(clip.start), (track.gain_db, track.muted))
            for track in doc.tracks
            for clip in track.clips
        }
    return {str(layer.id): (layer, value(layer.active.start), ()) for layer in doc.layers}


def interval(start, end, start_closed=True, end_closed=False):
    return {
        "start": encoded(start),
        "end": encoded(end),
        "start_closed": start_closed,
        "end_closed": end_closed,
    }


def rational(encoded_time):
    return Fraction(encoded_time["numerator"], encoded_time["denominator"])


def full(doc):
    if isinstance(doc, native_v2.CompositionDocument):
        return [interval(0, value(doc.duration))]
    return [
        interval(at, at + value(item.source_map.duration)) for item, at, _ in entries(doc).values()
    ]


def sampling_envelope(node, span):
    """Cover frame buckets and both directional boundary samples conservatively."""
    doc = model(node)
    if isinstance(doc, native_v2.CompositionDocument):
        fps = value(doc.frame_rate)
        duration = value(doc.duration)
    elif isinstance(doc, native_v2.AudioDocument):
        fps = Fraction(doc.sample_rate)
        duration = max(
            (at + value(item.source_map.duration) for item, at, _ in entries(doc).values()),
            default=Fraction(0),
        )
    else:
        return span
    start, end = rational(span["start"]), rational(span["end"])
    low = (start * fps).__floor__() / fps
    high_index = (end * fps).__floor__() + 1 if span["end_closed"] else ceil(end * fps)
    high = min(Fraction(high_index) / fps, duration)
    return interval(max(Fraction(0), low), high, True, True)


def compare(old, new, previous_ref, current_ref, derivations=()):
    derived = {item["document_id"]: item["derived_from"] for item in derivations}
    count = 0
    truncated = False

    class BudgetExceeded(Exception):
        pass

    def visit(left_ref, right_ref):
        nonlocal count, truncated
        if left_ref == right_ref:
            return [], []
        count += 1
        if count > LIMIT:
            raise BudgetExceeded()
        left = old[(left_ref["id"], left_ref["revision"])]
        right = new[(right_ref["id"], right_ref["revision"])]
        a, b = model(left), model(right)
        if a is None or b is None:
            return None, None  # Whole untimed source, or unsupported temporal format.
        if left["kind"] != right["kind"] or (
            left_ref["id"] != right_ref["id"] and derived.get(right_ref["id"]) != left_ref
        ):
            return full(a), full(b)
        # A document-wide timing/channel/canvas change affects its complete output.
        exclude = {"layers", "tracks", "sync"}
        if a.model_dump(exclude=exclude) != b.model_dump(exclude=exclude):
            return full(a), full(b)
        if isinstance(a, native_v2.CompositionDocument) and (
            [str(layer.id) for layer in a.layers] != [str(layer.id) for layer in b.layers]
        ):
            return full(a), full(b)
        left_items, right_items = entries(a), entries(b)
        results = [[], []]
        for identity in sorted(left_items.keys() | right_items.keys()):
            before, after = left_items.get(identity), right_items.get(identity)
            if before and after:
                x, _, track_x = before
                y, _, track_y = after
                settings_changed = x.model_dump() != y.model_dump() or track_x != track_y
                mask_x = left["inputs"].get(getattr(x, "mask", None))
                mask_y = right["inputs"].get(getattr(y, "mask", None))
                settings_changed = settings_changed or mask_x != mask_y
                if not settings_changed:
                    nested = visit(
                        left["inputs"][x.source]["target"], right["inputs"][y.source]["target"]
                    )
                else:
                    nested = (None, None)
            else:
                nested = (None, None)
            for side, item in enumerate((before, after)):
                if item is None:
                    continue
                instance, at, _ = item
                changed = nested[side]
                # Resampling/stretching can spread changes through filter windows.
                # Report the entire use rather than inventing a local DSP footprint.
                if (
                    changed
                    and isinstance(a if side == 0 else b, native_v2.AudioDocument)
                    and any(
                        abs(value(segment.rate)) != 1 for segment in instance.source_map.segments
                    )
                ):
                    changed = None
                if changed is None:
                    results[side].append(interval(at, at + value(instance.source_map.duration)))
                else:
                    source_ref = (left if side == 0 else right)["inputs"][instance.source]["target"]
                    source_node = (old if side == 0 else new)[
                        (source_ref["id"], source_ref["revision"])
                    ]
                    for span in changed:
                        span = sampling_envelope(source_node, span)
                        for mapped in affected_ranges(
                            instance.source_map,
                            rational(span["start"]),
                            rational(span["end"]),
                            start_closed=span["start_closed"],
                            end_closed=span["end_closed"],
                        ):
                            results[side].append(
                                interval(
                                    at + rational(mapped["start"]),
                                    at + rational(mapped["end"]),
                                    mapped["start_closed"],
                                    mapped["end_closed"],
                                )
                            )
                if len(results[side]) > LIMIT:
                    raise BudgetExceeded()
        return results

    try:
        before, after = visit(previous_ref, current_ref)
    except BudgetExceeded:
        truncated = True
        left = model(old[(previous_ref["id"], previous_ref["revision"])])
        right = model(new[(current_ref["id"], current_ref["revision"])])
        before, after = full(left) if left else None, full(right) if right else None

    def summarize(spans):
        if spans is None:
            return {"status": "unknown_or_untimed", "intervals": []}
        # Stable de-duplication; intervals retain endpoint closure for reverse/hold.
        unique = {str(span): span for span in spans}
        return {
            "status": "conservative",
            "intervals": sorted(
                unique.values(), key=lambda span: (rational(span["start"]), rational(span["end"]))
            ),
        }

    return {
        "before": summarize(before),
        "after": summarize(after),
        "truncated": truncated,
        "space": "each_output_revision",
        "historical_artifacts_invalidated": False,
    }
