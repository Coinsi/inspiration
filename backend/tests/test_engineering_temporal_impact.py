import copy
from fractions import Fraction

import pytest

from app.modules.engineering import temporal_impact
from tests.test_engineering_composition_v2_executor import mapped


def revised(mode):
    plan, _ = mapped(mode)
    newer = copy.deepcopy(plan)
    for node in newer["nodes"]:
        node["revision"] = 2
        for link in node["inputs"].values():
            link["target"]["revision"] = 2
    return (
        {(n["id"], n["revision"]): n for n in plan["nodes"]},
        {(n["id"], n["revision"]): n for n in newer["nodes"]},
        plan["root"],
        {**plan["root"], "revision": 2},
    )


def unpack(report):
    return [
        (
            temporal_impact.rational(s["start"]),
            temporal_impact.rational(s["end"]),
            s["start_closed"],
            s["end_closed"],
        )
        for s in report["after"]["intervals"]
    ]


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("repeat", [(Fraction(1, 2), 1, True, False), (Fraction(3, 2), 2, True, False)]),
        ("reverse", [(1, Fraction(3, 2), True, True)]),
        ("hold", [(0, 2, True, False)]),
    ],
)
def test_nested_changes_follow_repetition_reverse_and_hold(mode, expected):
    old, new, before, after = revised(mode)
    report = temporal_impact.compare(old, new, before, after)
    assert unpack(report) == expected
    assert report["before"] == report["after"]
    assert report["historical_artifacts_invalidated"] is False


def test_reverse_endpoint_survives_second_parent_mapping():
    from app.modules.engineering.timing import TimeMap, affected_ranges

    old, new, before, after = revised("reverse")
    report = temporal_impact.compare(old, new, before, after)
    span = {**report["after"]["intervals"][0], "start_closed": False}
    parent_map = TimeMap.model_validate(new[(after["id"], 2)]["payload"]["layers"][0]["source_map"])
    mapped_spans = affected_ranges(
        parent_map,
        temporal_impact.rational(span["start"]),
        temporal_impact.rational(span["end"]),
        start_closed=span["start_closed"],
        end_closed=span["end_closed"],
    )
    assert mapped_spans[0]["start_closed"] is True
    assert mapped_spans[0]["end_closed"] is False
    assert temporal_impact.rational(mapped_spans[0]["start"]) == Fraction(1, 2)
    assert temporal_impact.rational(mapped_spans[0]["end"]) == 1


def test_unchanged_output_and_unknown_formats():
    old, new, before, after = revised("repeat")
    assert unpack(temporal_impact.compare(old, old, before, before)) == []
    new[(after["id"], 2)]["kind"] = "vendor.unknown"
    assert (
        temporal_impact.compare(old, new, before, after)["after"]["status"] == "unknown_or_untimed"
    )


@pytest.mark.parametrize("mode", ["repeat", "reverse", "hold"])
def test_report_contains_every_changed_rendered_frame(mode):
    import hashlib
    import io

    from PIL import Image

    from app.modules.engineering import composition_v2_executor as comp
    from app.modules.engineering import documents, image_executor

    plan, raw = mapped(mode)
    old = {(n["id"], n["revision"]): n for n in plan["nodes"]}
    new_plan = copy.deepcopy(plan)
    blue = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(blue, format="PNG")
    for node in new_plan["nodes"]:
        node["revision"] = 2
        for link in node["inputs"].values():
            link["target"]["revision"] = 2
        if node["kind"] == "resource":
            node["payload"]["blob_hash"] = hashlib.sha256(blue.getvalue()).hexdigest()
        node["content_hash"] = documents.digest(node["inputs"], node["payload"])
    new_plan["root"]["revision"] = 2
    new = {(n["id"], n["revision"]): n for n in new_plan["nodes"]}
    report = temporal_impact.compare(old, new, plan["root"], new_plan["root"])
    spans = unpack(report)
    for index in range(4):
        time = Fraction(index, 2)
        before = image_executor.render(comp.frame_plan(plan, time), lambda _: raw)
        after = image_executor.render(comp.frame_plan(new_plan, time), lambda _: blue.getvalue())
        if before != after:
            assert any(
                (time > a or (time == a and ac)) and (time < b or (time == b and bc))
                for a, b, ac, bc in spans
            )


def test_impact_budget_falls_back_to_whole_output(monkeypatch):
    monkeypatch.setattr(temporal_impact, "LIMIT", 1)
    old, new, before, after = revised("repeat")
    report = temporal_impact.compare(old, new, before, after)
    assert report["truncated"] is True
    assert unpack(report) == [(0, 2, True, False)]


def test_audio_dsp_expands_nested_change_to_whole_use(tmp_path):
    import uuid

    from app.modules.engineering.timing import encoded
    from tests.test_engineering_audio_v2_executor import fixture_plan

    plan, _ = fixture_plan(tmp_path, "stretch", [(0, 0.25, 0, 2)])
    root = plan["nodes"][-1]
    child = copy.deepcopy(root)
    child["id"] = str(uuid.uuid4())
    clip = child["payload"]["tracks"][0]["clips"][0]
    clip["start"] = encoded(0.25)
    clip["source_map"]["audio_policy"] = "resample"
    clip["source_map"]["segments"][0]["rate"] = encoded(1)
    root["inputs"]["voice"] = {
        "target": {"id": child["id"], "revision": 1},
        "accepts": "audio.document",
    }
    plan["nodes"].insert(1, child)
    old = {(n["id"], n["revision"]): n for n in plan["nodes"]}
    new = copy.deepcopy(old)
    for item in (child, root):
        changed = new.pop((item["id"], 1))
        changed["revision"] = 2
        new[(item["id"], 2)] = changed
    new[(child["id"], 2)]["payload"]["tracks"][0]["clips"][0]["gain_db"] = -6
    new[(root["id"], 2)]["inputs"]["voice"]["target"]["revision"] = 2
    report = temporal_impact.compare(old, new, plan["root"], {**plan["root"], "revision": 2})
    assert unpack(report) == [(0, Fraction(1, 4), True, False)]
