"""Exact-pin object reference selection; never create substitute embeddings."""

import functools
import hashlib
import inspect
import math
from numbers import Real

from wise_bridge import WISE_COMMIT
from wise_bridge.query_errors import ReferenceQueryError

PATCH_COMMIT = "fcfa443fbb46eb361bb19151339338616838a5b5"
REGION_SHA256 = "7cad98a3e438f97bc97dac54e2264b927ee4278fd1adae7e1e13a4276a637937"
SELECTION_SHA256 = "07d07ea38a2eb6b5c7f8d3c02f3243f05eab3ff3b1be2309e20198a7e2074268"


def clip_bbox_xywh(box):
    """Intersect original-image normalized XYWH with the visible frame.

    WISE already removes OWLv2's square padding, but leaves extents unclipped.
    Both returned regions and reference selection use this same geometry.
    """
    try:
        values = tuple(box)
    except TypeError:
        return None
    if len(values) != 4 or any(
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        for value in values
    ):
        return None
    x, y, width, height = (float(value) for value in values)
    right, bottom = x + width, y + height
    if width <= 0 or height <= 0 or not all(map(math.isfinite, (right, bottom))):
        return None
    left, top = max(0.0, x), max(0.0, y)
    right, bottom = min(1.0, right), min(1.0, bottom)
    if right <= left or bottom <= top:
        return None
    return {"x": left, "y": top, "w": right - left, "h": bottom - top}


def select_overlapping_region(metadata, region):
    target = clip_bbox_xywh(region)
    if target is None:
        raise ValueError("Invalid reference region")
    best_index, best_iou, valid = None, 0.0, 0
    for index, item in enumerate(metadata):
        box = clip_bbox_xywh(item.bbox)
        if box is None:
            continue
        valid += 1
        width = max(
            0.0,
            min(box["x"] + box["w"], target["x"] + target["w"])
            - max(box["x"], target["x"]),
        )
        height = max(
            0.0,
            min(box["y"] + box["h"], target["y"] + target["h"])
            - max(box["y"], target["y"]),
        )
        intersection = width * height
        union = box["w"] * box["h"] + target["w"] * target["h"] - intersection
        iou = intersection / union if union > 0 else 0.0
        if iou > best_iou:
            best_index, best_iou = index, iou
    if not valid:
        raise ReferenceQueryError("reference_no_object")
    if best_index is None:
        raise ReferenceQueryError("reference_region_no_match")
    return best_index


def region_method(original, selection_original):
    """Override only OWLv2's region method, after checking both pinned methods."""
    if WISE_COMMIT != PATCH_COMMIT or any(
        hashlib.sha256(inspect.getsource(method).encode()).hexdigest() != expected
        for method, expected in (
            (original, REGION_SHA256),
            (selection_original, SELECTION_SHA256),
        )
    ):
        raise RuntimeError("WISE object region source changed; review required")

    @functools.wraps(original)
    def matching_region(self, image, region):
        from wise.feature.feature_extractor import Features

        # One genuine extraction, retaining the original vector/metadata pairing.
        # Empty or disjoint detections must never fall through to argmax(0).
        results = self.extract_image_features(image)
        if not isinstance(results, list) or len(results) != 1:
            raise RuntimeError("WISE object feature result structure changed")
        features = results[0]
        if len(features.vectors) != len(features.metadata):
            raise RuntimeError("WISE object vectors and metadata are inconsistent")
        index = select_overlapping_region(features.metadata, region)
        return Features(
            vectors=features.vectors[[index], :], metadata=[features.metadata[index]]
        )

    matching_region._inspiration_object_region = 1
    return matching_region
