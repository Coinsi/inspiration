"""Real store/worker identity gates, with an explicit non-inference engine stub."""

import hashlib
import json
import uuid

import pytest
from test_bridge import FakeEngine

from wise_bridge import MODELS
from wise_bridge.config import Settings
from wise_bridge.runner import EngineFailure, Worker
from wise_bridge.store import Store


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    project, source, job_id = (str(uuid.uuid4()) for _ in range(3))
    path = tmp_path / (source + ".mp4")
    path.write_bytes(b"explicit non-inference fixture")
    settings = Settings(data_dir=tmp_path, min_free_bytes=0)
    store = Store(tmp_path)
    engine = FakeEngine()
    worker = Worker(settings, store, engine)
    store.enqueue(
        job_id,
        project,
        source,
        "upsert",
        {
            "file": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "media_type": "video",
            "capabilities": ["object"],
        },
        10,
    )
    worker.process(store.claim())
    assert store.job(job_id)["state"] == "ready"
    snapshot = tmp_path / "projects" / project / "snapshots" / store.head(project)
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["signature"] = {
        "models": {"object": MODELS["object"]},
        "optional_models": {"object": "a" * 64},
    }
    manifest_path.write_text(json.dumps(manifest))
    state = {"ready": True, "feature_fingerprint": "a" * 64}
    monkeypatch.setattr(
        "wise_bridge.runner.inspect_optional", lambda *args: state.copy()
    )
    engine.requests.clear()
    return worker, engine, project, source, manifest_path, state


def query(worker, project, **values):
    return worker.search(
        project, {"modality": "object", "query": "object", "limit": 20, **values}
    )


def test_valid_feature_space_supplies_internal_identity_to_query_child(indexed):
    worker, engine, project, source, _, _ = indexed
    result = query(worker, project, feature_fingerprint="client cannot override this")
    assert {item["source_id"] for item in result["items"]} == {source}
    assert engine.requests[0][0] == "search"
    assert engine.requests[0][1]["feature_fingerprint"] == "a" * 64


@pytest.mark.parametrize(
    "change", ["weights", "config", "legacy", "model", "unvalidated"]
)
def test_unmatched_or_unvalidated_optional_index_never_reaches_query_engine(
    indexed, change
):
    worker, engine, project, _, path, state = indexed
    manifest = json.loads(path.read_text())
    if change in ("weights", "config"):
        # Both changes receive a different stable feature fingerprint from the
        # optional preflight. A fresh receipt alone cannot upgrade existing data.
        state["feature_fingerprint"] = "b" * 64
    elif change == "legacy":
        del manifest["signature"]["optional_models"]
    elif change == "model":
        manifest["signature"]["models"]["object"] = "obsolete-model"
    else:
        state["ready"] = False
    path.write_text(json.dumps(manifest))
    with pytest.raises(EngineFailure, match="offline probe|rebuild"):
        query(worker, project)
    assert not engine.requests


def test_model_replacement_during_search_discards_results(indexed):
    worker, engine, project, _, _, state = indexed
    operation = engine.operation

    def replace_during_query(*args, **kwargs):
        result = operation(*args, **kwargs)
        state["feature_fingerprint"] = "b" * 64
        return result

    engine.operation = replace_during_query
    with pytest.raises(EngineFailure, match="rebuild"):
        query(worker, project)
    assert len(engine.requests) == 1


def test_empty_scope_does_not_open_an_optional_encoder(indexed):
    worker, engine, project, _, _, state = indexed
    state["ready"] = False
    assert query(worker, project, source_ids=[])["items"] == []
    assert not engine.requests
