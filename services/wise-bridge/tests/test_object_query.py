"""Deterministic selection/guard tests, not a substitute for genuine inference."""

import hashlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from test_search_media_time import NoFeaturesFoundError, query_fixture

from wise_bridge import object_query
from wise_bridge.query_errors import ReferenceQueryError


def metadata(*boxes):
    return [SimpleNamespace(bbox=box) for box in boxes]


@pytest.mark.parametrize(
    "boxes", [[], [(2, 2, 0.1, 0.1)], [(0, 0, float("nan"), 1)], [(0, 0, 0, 1)]]
)
def test_no_visible_detection_is_a_reference_error(boxes):
    with pytest.raises(ReferenceQueryError) as error:
        object_query.select_overlapping_region(metadata(*boxes), (0, 0, 1, 1))
    assert error.value.code == "reference_no_object"


@pytest.mark.parametrize("box", [(0.5, 0.5, 0.1, 0.1), (0.2, 0, 0.2, 0.2)])
def test_disjoint_or_edge_touching_region_never_selects_first_detection(box):
    with pytest.raises(ReferenceQueryError) as error:
        object_query.select_overlapping_region(metadata(box), (0, 0, 0.2, 0.2))
    assert error.value.code == "reference_region_no_match"


def test_positive_overlap_selects_highest_visible_iou_and_preserves_first_tie():
    boxes = metadata((0.5, 0.5, 0.2, 0.2), (-0.3, 0, 0.5, 0.2), (0, 0, 0.2, 0.2))
    assert object_query.select_overlapping_region(boxes, (0, 0, 0.2, 0.2)) == 1
    # No minimum arbitrary quality threshold: a genuinely positive intersection
    # remains usable, but a zero-area one never is.
    assert (
        object_query.select_overlapping_region(
            metadata((0.19, 0, 0.2, 0.2)), (0, 0, 0.2, 0.2)
        )
        == 0
    )


def original(self, image, region):
    raise AssertionError("The unsafe pinned selection must not run")


def selection_original(self, image, region):
    raise AssertionError("The unsafe pinned selection must not run")


@pytest.fixture
def wrapped(monkeypatch):
    fixtures = Path(__file__).parent / "fixtures"
    source = (fixtures / "pinned_object_region.txt").read_text()
    selection = (fixtures / "pinned_region_selection.txt").read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == object_query.REGION_SHA256
    assert (
        hashlib.sha256(selection.encode()).hexdigest() == object_query.SELECTION_SHA256
    )
    monkeypatch.setattr(
        object_query.inspect,
        "getsource",
        lambda method: source if method is original else selection,
    )
    module = ModuleType("wise.feature.feature_extractor")
    module.Features = SimpleNamespace
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return object_query.region_method(original, selection_original)


class Vectors:
    def __init__(self, values):
        self.values = values

    def __len__(self):
        return len(self.values)

    def __getitem__(self, key):
        indices, axis = key
        assert axis == slice(None)
        return [self.values[index] for index in indices]


def test_guarded_method_extracts_once_and_returns_exact_original_vector_and_metadata(
    wrapped,
):
    vectors = [object(), object(), object()]
    boxes = metadata(
        (0.7, 0.7, 0.1, 0.1), (0.2, 0.2, 0.3, 0.3), (0.25, 0.25, 0.05, 0.05)
    )
    calls = []
    result = SimpleNamespace(vectors=Vectors(vectors), metadata=boxes)
    extractor = SimpleNamespace(
        extract_image_features=lambda image: calls.append(image) or [result]
    )
    image = object()
    match = wrapped(extractor, image, (0.2, 0.2, 0.3, 0.3))
    assert calls == [image]
    assert match.vectors == [vectors[1]] and match.metadata[0] is boxes[1]


def test_empty_extract_and_inconsistent_structure_have_different_failure_types(wrapped):
    extractor = SimpleNamespace(
        extract_image_features=lambda _: [
            SimpleNamespace(vectors=Vectors([]), metadata=[])
        ]
    )
    with pytest.raises(ReferenceQueryError, match="reference_no_object"):
        wrapped(extractor, object(), (0, 0, 1, 1))
    extractor.extract_image_features = lambda _: [
        SimpleNamespace(vectors=Vectors([object()]), metadata=[])
    ]
    with pytest.raises(RuntimeError, match="inconsistent"):
        wrapped(extractor, object(), (0, 0, 1, 1))


def test_unknown_upstream_region_implementation_is_rejected():
    with pytest.raises(RuntimeError, match="source changed"):
        object_query.region_method(original, selection_original)


def test_wrong_commit_is_rejected_even_with_matching_functions(monkeypatch, wrapped):
    monkeypatch.setattr(object_query, "WISE_COMMIT", "unexpected")
    with pytest.raises(RuntimeError, match="source changed"):
        object_query.region_method(original, selection_original)


@pytest.mark.parametrize("changed", [original, selection_original])
def test_each_pinned_function_is_verified_before_installing_wrapper(
    monkeypatch, wrapped, changed
):
    reader = object_query.inspect.getsource
    monkeypatch.setattr(
        object_query.inspect,
        "getsource",
        lambda method: reader(method) + ("\n" if method is changed else ""),
    )
    with pytest.raises(RuntimeError, match="source changed"):
        object_query.region_method(original, selection_original)


@pytest.mark.parametrize(
    "capability,reference,expected",
    [
        ("object", True, ReferenceQueryError),
        ("object", False, NoFeaturesFoundError),
        ("visual", True, NoFeaturesFoundError),
    ],
)
def test_only_object_reference_no_features_gets_expected_input_error(
    monkeypatch, tmp_path, capability, reference, expected
):
    path = tmp_path / "reference.png"
    path.write_bytes(b"structural fixture; no image inference")
    with pytest.raises(expected):
        query_fixture(
            monkeypatch,
            capability,
            0,
            embedding_error=NoFeaturesFoundError("private detail"),
            request_extra={"query_file": str(path)} if reference else {},
        )


def test_unexpected_embedding_failure_is_not_misreported_as_no_object(
    monkeypatch, tmp_path
):
    path = tmp_path / "reference.png"
    path.write_bytes(b"structural fixture")
    with pytest.raises(RuntimeError, match="GPU failure"):
        query_fixture(
            monkeypatch,
            "object",
            0,
            embedding_error=RuntimeError("GPU failure"),
            request_extra={"query_file": str(path)},
        )
