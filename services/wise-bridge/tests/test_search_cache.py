"""Cache isolation and embedding call-count tests, not retrieval-quality tests."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from wise_bridge import MODELS
from wise_bridge.search_cache import ProjectEntry, SearchCache
from wise_bridge.upstream_worker import search


def request(tmp_path, project="one", revision="one", size=10):
    path = tmp_path / project / revision
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.faiss").write_bytes(b"x" * size)
    return {"project_id": project, "revision": revision, "project_dir": str(path)}


@pytest.fixture
def cache(monkeypatch):
    disposed = []

    def load(path, size):
        project = SimpleNamespace(
            db_engine=SimpleNamespace(dispose=lambda: disposed.append(path))
        )
        return ProjectEntry(project, object(), object(), {}, size)

    result = SearchCache(max_projects=2, max_index_bytes=25)
    monkeypatch.setattr(result, "_load_project", load)
    return result, disposed


def test_same_revision_reuses_entry_and_new_revision_disposes_old(tmp_path, cache):
    store, disposed = cache
    first_request = request(tmp_path)
    first = store.project(first_request)
    assert store.project(first_request) is first
    second = store.project(request(tmp_path, revision="two"))
    assert second is not first
    assert len(store.projects) == 1
    assert len(disposed) == 1


def test_project_lru_and_total_index_bytes_are_bounded(tmp_path, cache):
    store, disposed = cache
    first_request = request(tmp_path)
    store.project(first_request)
    store.project(request(tmp_path, project="two"))
    store.project(first_request)
    store.project(request(tmp_path, project="three"))
    assert {key[0] for key in store.projects} == {"one", "three"}
    assert len(disposed) == 1
    store.project(request(tmp_path, project="four", size=20))
    assert {key[0] for key in store.projects} == {"four"}
    assert len(disposed) == 3


def test_single_oversized_index_is_rejected_before_load(tmp_path, cache):
    store, _ = cache
    with pytest.raises(RuntimeError, match="budget"):
        store.project(request(tmp_path, size=26))
    assert not store.projects


def test_embedding_service_is_reused_and_model_budget_is_enforced(monkeypatch):
    created = []
    module = ModuleType("wise.api.services.embedding")
    module.EmbeddingService = SimpleNamespace(
        from_ids=lambda ids, config: created.append(ids) or object()
    )
    monkeypatch.setitem(sys.modules, module.__name__, module)
    cache = SearchCache(max_models=1)
    first = cache.embedding(MODELS["visual"], {})
    assert cache.embedding(MODELS["visual"], {}) is first
    assert len(created) == 1
    with pytest.raises(RuntimeError, match="restart required"):
        cache.embedding(MODELS["audio"], {})
    assert len(created) == 1


def test_same_model_feature_identity_cannot_reuse_cached_embedding(monkeypatch):
    created = []
    module = ModuleType("wise.api.services.embedding")
    module.EmbeddingService = SimpleNamespace(
        from_ids=lambda ids, config: created.append((ids, config)) or object()
    )
    monkeypatch.setitem(sys.modules, module.__name__, module)
    cache = SearchCache()
    first = cache.embedding(MODELS["object"], {}, feature_fingerprint="a" * 64)
    assert cache.embedding(MODELS["object"], {}, feature_fingerprint="a" * 64) is first
    for fingerprint in ("b" * 64, None):
        with pytest.raises(RuntimeError, match="fingerprint changed; restart"):
            cache.embedding(MODELS["object"], {}, feature_fingerprint=fingerprint)
    assert len(created) == 1


@pytest.mark.parametrize("multiple_boxes", [False, True])
def test_mixed_image_video_query_embeds_once_and_filters_before_search(
    monkeypatch, multiple_boxes
):
    # A structural stub exposes the exact pinned WISE method signatures. The
    # separate opt-in live test runs these calls with genuine WISE models.
    # Optional inference is deliberately blocked in production; this case tests
    # result transport with structural objects only, not model readiness.
    monkeypatch.setattr(
        "wise_bridge.optional_models.enforce_optional_runtime",
        lambda *args, **kwargs: None,
    )

    def module(name, **attrs):
        value = ModuleType(name)
        value.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, value)

    module("numpy", asarray=lambda values, dtype: values, int64=int)
    module(
        "wise.api.common",
        BBoxXYWH=SimpleNamespace,
        MediaQueryTerm=SimpleNamespace,
        TextQueryTerm=SimpleNamespace,
    )
    module("wise.api.services.embedding", EmbeddingConfig=SimpleNamespace)
    module(
        "wise.api.services.embedding.exceptions",
        NoFeaturesFoundError=type("NoFeaturesFoundError", (Exception,), {}),
    )
    module(
        "wise.data_models",
        ModalityType=SimpleNamespace(IMAGE="image", VIDEO="video", AUDIO="audio"),
    )
    encoded, calls = [], []
    capability = "object" if multiple_boxes else "visual"
    allowed_vectors = [7, 8] if multiple_boxes else [7]
    feature = object()
    embedding = SimpleNamespace(embed=lambda *args: encoded.append(args) or feature)

    class SearchService:
        def __init__(self, project, embedding):
            self.search_indices = {
                kind: {MODELS[capability]: object()} for kind in ("image", "video")
            }

        def search_with_feature(
            self, features, kind, model, start, end, filters, vector_ids
        ):
            assert features is feature
            assert vector_ids == allowed_vectors
            calls.append(kind)
            if multiple_boxes and kind == "video":
                return SimpleNamespace(
                    metadata=[
                        SimpleNamespace(media_id=1, timestamp=1.5, end_timestamp=None)
                    ]
                    * 2,
                    ids=[7, 8],
                    distances=[0.8, 0.7],
                    ext_metadata=[
                        SimpleNamespace(bbox=(0.1, 0.2, 0.3, 0.4)),
                        SimpleNamespace(bbox=(0.6, 0.5, 0.2, 0.3)),
                    ],
                )
            return SimpleNamespace(metadata=[], distances=[], ext_metadata=[])

    module("wise.api.services.search.local", LocalSearchService=SearchService)
    entry = SimpleNamespace(
        project=SimpleNamespace(
            get_vector_ids=lambda media_ids, *args: (
                allowed_vectors if media_ids == [1] else []
            )
        ),
        mapping={"1": "allowed", "2": "hidden"},
        service=object(),
        config=SimpleNamespace(feature_extractor_config={}),
    )
    cache = SimpleNamespace(
        project=lambda request: entry, embedding=lambda *args, **kwargs: embedding
    )
    results = search(
        "unused",
        {
            "modality": capability,
            "feature_fingerprint": "a" * 64 if multiple_boxes else None,
            "source_ids": ["allowed"],
            "query": "blue",
            "limit": 10,
        },
        cache,
    )
    if multiple_boxes:
        assert [row["vector_id"] for row in results] == ["7", "8"]
        assert [row["start_seconds"] for row in results] == [1.5, 1.5]
        assert [row["bbox"]["x"] for row in results] == [0.1, 0.6]
    else:
        assert results == []
    assert len(encoded) == 1
    assert calls == ["image", "video"]


def test_optional_query_is_rejected_before_any_upstream_model_import(monkeypatch):
    from wise_bridge.optional_models import OptionalModelUnavailable

    def reject(caps, *, expected_fingerprint=None):
        assert caps == ["object"]
        raise OptionalModelUnavailable("object", "validation_required")

    monkeypatch.setattr("wise_bridge.optional_models.enforce_optional_runtime", reject)
    with pytest.raises(OptionalModelUnavailable, match="validation_required"):
        search("unused", {"modality": "object"})


def test_optional_query_missing_identity_fails_before_model_import(monkeypatch):
    monkeypatch.setattr(
        "wise_bridge.optional_models.enforce_optional_runtime",
        lambda *args, **kwargs: None,
    )
    # Even a model accepted by the preflight cannot silently query an old request
    # without identifying the index/encoder feature space.
    with pytest.raises(ValueError, match="verified feature fingerprint"):
        search("unused", {"modality": "object"})
