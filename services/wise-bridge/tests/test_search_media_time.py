"""Pinned search-result shape regression, without loading an inference model."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from wise_bridge import MODELS
from wise_bridge.upstream_worker import search


class NoFeaturesFoundError(Exception):
    pass


@pytest.mark.parametrize("capability", ["visual", "object"])
@pytest.mark.parametrize("image_time", [None, 0.0])
def test_image_index_never_exposes_a_timestamp_and_video_keeps_its_frame_time(
    monkeypatch, capability, image_time
):
    result = query_fixture(monkeypatch, capability, image_time)
    image = [row for row in result if row["source_id"] == "image"]
    video = [row for row in result if row["source_id"] == "video"]
    assert len(image) == 2 and len(video) == 2
    assert all(
        row["start_seconds"] is None
        and row["end_seconds"] is None
        and row["match_type"] == "image"
        for row in image
    )
    assert all(
        row["start_seconds"] == 1.5
        and row["end_seconds"] == 1.5
        and row["match_type"] == "frame"
        for row in video
    )
    assert [row["vector_id"] for row in image] == ["11", "12"]
    assert [row["vector_id"] for row in video] == ["21", "22"]
    if capability == "object":
        assert [row["bbox"]["x"] for row in image] == [0.1, 0.6]
        assert [row["bbox"]["x"] for row in video] == [0.1, 0.6]


def test_audio_interval_is_not_normalized_as_a_still_image(monkeypatch):
    result = query_fixture(monkeypatch, "audio", 0.0)
    assert len(result) == 2
    assert all(
        row["start_seconds"] == 0.0
        and row["end_seconds"] == 4.0
        and row["match_type"] == "segment"
        for row in result
    )


def query_fixture(
    monkeypatch,
    capability,
    image_time,
    boxes=None,
    *,
    embedding_error=None,
    request_extra=None,
):
    # These are explicit structural doubles for the pinned upstream API. Real
    # extraction/image timestamps were observed in the failed GPU object probe.
    # The opt-in probe remains strict and is not replaced by this unit regression.
    monkeypatch.setattr(
        "wise_bridge.optional_models.enforce_optional_runtime",
        lambda *args, **kwargs: None,
    )

    def module(name, **values):
        imported = ModuleType(name)
        imported.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, imported)

    module("numpy", asarray=lambda value, dtype: value, int64=int)
    module(
        "wise.api.common",
        BBoxXYWH=SimpleNamespace,
        MediaQueryTerm=SimpleNamespace,
        TextQueryTerm=SimpleNamespace,
    )
    module("wise.api.services.embedding", EmbeddingConfig=SimpleNamespace)
    module(
        "wise.api.services.embedding.exceptions",
        NoFeaturesFoundError=NoFeaturesFoundError,
    )
    module(
        "wise.data_models",
        ModalityType=SimpleNamespace(IMAGE="image", VIDEO="video", AUDIO="audio"),
    )
    kinds = ["audio"] if capability == "audio" else ["image", "video"]
    by_kind = {"image": [11, 12], "video": [21, 22], "audio": [31, 32]}
    calls, embeddings = [], []

    def embed(*args):
        embeddings.append(args)
        if embedding_error:
            raise embedding_error
        return object()

    embedding = SimpleNamespace(embed=embed)

    class SearchService:
        def __init__(self, *args):
            self.search_indices = {
                kind: {MODELS[capability]: object()} for kind in kinds
            }

        def search_with_feature(
            self, features, kind, model, start, end, filters, vector_ids
        ):
            assert vector_ids == by_kind[kind]
            calls.append(kind)
            mid = {"image": 1, "video": 2, "audio": 3}[kind]
            timestamp = (
                image_time if kind == "image" else 1.5 if kind == "video" else 0.0
            )
            return SimpleNamespace(
                metadata=[
                    SimpleNamespace(
                        media_id=mid,
                        timestamp=timestamp,
                        end_timestamp=4.0 if kind == "audio" else None,
                    )
                ]
                * 2,
                ids=by_kind[kind],
                distances=[0.8, 0.7],
                ext_metadata=[
                    SimpleNamespace(bbox=box if capability == "object" else None)
                    for box in (
                        boxes
                        if boxes is not None
                        else [(0.1, 0.2, 0.3, 0.4), (0.6, 0.5, 0.2, 0.3)]
                    )
                ],
            )

    module("wise.api.services.search.local", LocalSearchService=SearchService)
    entry = SimpleNamespace(
        project=SimpleNamespace(get_vector_ids=lambda mids, kind, model: by_kind[kind]),
        mapping={"1": "image", "2": "video", "3": "audio"},
        service=object(),
        config=SimpleNamespace(feature_extractor_config={}),
    )
    cache = SimpleNamespace(
        project=lambda request: entry, embedding=lambda *args, **kwargs: embedding
    )
    result = search(
        "explicit-test-fixture",
        {
            "modality": capability,
            "feature_fingerprint": "a" * 64 if capability == "object" else None,
            "source_ids": kinds,
            "query": "cup",
            "limit": 20,
            **(request_extra or {}),
        },
        cache,
    )
    assert calls == kinds and len(embeddings) == 1
    return result
