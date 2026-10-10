"""Geometry and production result transport; no model inference substitutes."""

import math

import pytest
from test_search_media_time import query_fixture

from wise_bridge.upstream_worker import clip_bbox_xywh


@pytest.mark.parametrize(
    "box,expected",
    [
        ((0.2, 0.3, 0.4, 0.5), (0.2, 0.3, 0.4, 0.5)),
        ((-0.1, 0.2, 0.4, 0.5), (0.0, 0.2, 0.3, 0.5)),
        ((0.75, 0.8, 0.5, 0.4), (0.75, 0.8, 0.25, 0.2)),
        ((-0.2, -0.3, 1.5, 1.7), (0.0, 0.0, 1.0, 1.0)),
        # Representative 768x512 input: WISE already removed square padding
        # and converted center XYWH to top-left XYWH. Do not convert a second time.
        ((-0.05, -0.075, 0.5, 0.6), (0.0, 0.0, 0.45, 0.525)),
        ((0.2, 0.1, 0.8000000000000002, 0.900000000000001), (0.2, 0.1, 0.8, 0.9)),
    ],
)
def test_normalized_xywh_is_intersected_with_the_actual_frame(box, expected):
    result = clip_bbox_xywh(box)
    assert tuple(result[name] for name in ("x", "y", "w", "h")) == pytest.approx(
        expected
    )
    assert 0 <= result["x"] < 1 and 0 <= result["y"] < 1
    assert result["w"] > 0 and result["h"] > 0
    assert result["x"] + result["w"] <= 1
    assert result["y"] + result["h"] <= 1


@pytest.mark.parametrize(
    "box",
    [
        None,
        (),
        (0, 0, 1),
        (0, 0, 1, 1, 1),
        (float("nan"), 0, 0.2, 0.2),
        (0, float("inf"), 0.2, 0.2),
        (0, 0, float("inf"), 1),
        (0, 0, 1, float("nan")),
        (True, 0, 0.2, 0.2),
        ("0", 0, 0.2, 0.2),
        (0.5, 0.5, 0, 0.5),
        (0.5, 0.5, 0.5, -0.1),
        (-0.5, 0.2, 0.4, 0.2),
        (1, 0.2, 0.1, 0.2),
        (0.2, -0.5, 0.2, 0.4),
        (0.2, 1, 0.2, 0.1),
        (-1, -1, 1, 1),
        (1e308, 0.2, 1e308, 0.2),
    ],
)
def test_invalid_and_entirely_outside_regions_have_no_visible_area(box):
    assert clip_bbox_xywh(box) is None


def test_clipped_object_boxes_keep_vector_identity_and_image_video_time(monkeypatch):
    rows = query_fixture(
        monkeypatch,
        "object",
        0.0,
        boxes=[(-0.1, 0.2, 0.4, 0.5), (0.75, 0.8, 0.5, 0.4)],
    )
    assert {row["vector_id"] for row in rows} == {"11", "12", "21", "22"}
    for row in rows:
        expected = (
            (0, 0.2, 0.3, 0.5)
            if row["vector_id"].endswith("1")
            else (0.75, 0.8, 0.25, 0.2)
        )
        assert all(
            math.isclose(actual, target, rel_tol=1e-9, abs_tol=1e-12)
            for actual, target in zip(row["bbox"].values(), expected, strict=True)
        )
        if row["source_id"] == "image":
            assert row["start_seconds"] is None and row["end_seconds"] is None
        else:
            assert row["start_seconds"] == row["end_seconds"] == 1.5


@pytest.mark.parametrize("invalid", [None, (1, 0.2, 0.1, 0.2), (0, 0, 0, 1)])
def test_unusable_regions_are_filtered_without_losing_other_same_frame_objects(
    monkeypatch, invalid
):
    rows = query_fixture(
        monkeypatch, "object", 0.0, boxes=[invalid, (-0.1, 0.2, 0.4, 0.5)]
    )
    assert {row["vector_id"] for row in rows} == {"12", "22"}
    assert all(row["score"] == 0.7 for row in rows)
    assert all(
        math.isclose(actual, target, rel_tol=1e-9, abs_tol=1e-12)
        for row in rows
        for actual, target in zip(row["bbox"].values(), (0, 0.2, 0.3, 0.5), strict=True)
    )
