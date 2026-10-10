"""Validate metric arithmetic/isolation, never assert that an ML ranking must pass."""

# ruff: noqa: F811
import json
import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from PIL import Image

from scripts import evaluate_media_retrieval as evaluate
from tests.test_media_benchmark import short_source  # noqa: F401


def trace(names):
    return {
        "raw_hits": [{"name": name} for name in names],
        "ranked_versions": [{"name": name, "score": 0.1} for name in names],
    }


def query(relevant):
    return {"id": "cup-zh", "language": "zh", "query": "杯子", "relevant": relevant}


def test_video_frames_cannot_fill_unique_version_top_k():
    hits = [{"version_id": "video", "score": 1.0} for _ in range(10)]
    hits.append({"version_id": "image", "score": 0.9})
    ranked = evaluate.rank_versions(hits, {"video": "cup-video", "image": "cup-image"})
    observed = evaluate.score_trace(
        query(["cup-image"]), {"raw_hits": hits, "ranked_versions": ranked}
    )
    assert observed["unique_hit_count"] == 2
    assert observed["reciprocal_rank"] == 0.5
    assert observed["recall"] == {"1": 0.0, "2": 1.0, "3": 1.0}


def test_recall_denominator_includes_all_relevant_versions_and_real_misses():
    observed = evaluate.score_trace(
        query(["cup-image", "cup-video"]), trace(["fantasy-image", "cup-video"])
    )
    assert observed["recall"] == {"1": 0.0, "2": 0.5, "3": 0.5}
    assert observed["reciprocal_rank"] == 0.5
    absent = evaluate.score_trace(query(["cup-video"]), trace(["cup-image", "fantasy-image"]))
    assert absent["reciprocal_rank"] == 0 and absent["recall"]["3"] == 0


def test_empty_relevance_is_unscored_even_when_nearest_neighbor_is_returned():
    observed = evaluate.score_trace(query([]), trace(["cup-video"]))
    assert observed["scored"] is False
    assert "reciprocal_rank" not in observed and "recall" not in observed
    assert observed["unique_hit_count"] == 1
    assert observed["top_score"] == 0.1


def test_foreign_version_and_nonfinite_scores_fail_infrastructure_checks():
    with pytest.raises(evaluate.bench.BenchmarkError, match="foreign"):
        evaluate.rank_versions([{"version_id": "other", "score": 1}], {"ours": "cup-image"})
    with pytest.raises(evaluate.bench.BenchmarkError, match="score"):
        evaluate.rank_versions(
            [{"version_id": "ours", "score": float("nan")}], {"ours": "cup-image"}
        )


def test_saved_report_excludes_negative_queries_and_has_no_quality_threshold(tmp_path):
    positive, negative = (
        query(["cup-image"]),
        {**query([]), "id": "fox-en", "language": "en", "query": "fox"},
    )
    evidence = evaluate.bench.Evidence(
        tmp_path, {"queries": [positive, negative], "limitations": []}
    )
    (tmp_path / "traces").mkdir()
    for q in [positive, negative]:
        evaluate.bench.atomic_json(
            tmp_path / "traces" / f"{q['id']}.json", {"query": q["query"], **trace(["cup-image"])}
        )
    result = evaluate.score_saved(evidence)
    assert result["metrics"]["all"]["query_count"] == 1
    assert result["metrics"]["all"]["mrr"] == 1
    assert result["metrics"]["en"]["mrr"] is None
    assert result["quality_pass_threshold"] is None


def test_interrupted_raw_trace_is_resumed_without_another_model_request(tmp_path):
    state = {
        "run_id": "a" * 32,
        "plan": {},
        "queries": [query(["cup-image"])],
        "samples": [{"version_id": "our-version", "name": "cup-image"}],
    }
    evidence = evaluate.bench.Evidence(tmp_path, state)
    (tmp_path / "traces").mkdir()
    path = tmp_path / "traces" / "cup-zh.json"
    evaluate.bench.atomic_json(
        path, {"query": "杯子", "raw_hits": [{"version_id": "our-version", "score": 0.4}]}
    )
    # A client was never connected; any network/API call would fail this test.
    evaluate.EvaluationPipeline(evidence, evaluate.parse_args([])).query({})
    assert json.loads(path.read_text())["ranked_versions"][0]["name"] == "cup-image"


@pytest.mark.parametrize("bad", ["../escape", "same"])
def test_query_labels_are_scoped_and_paths_bounded(bad):
    queries = [{**query(["cup-image"]), "id": bad}, {**query(["cup-image"]), "id": "same"}]
    with pytest.raises(evaluate.bench.BenchmarkError):
        evaluate.validate_queries(queries, {"cup-image"})
    with pytest.raises(evaluate.bench.BenchmarkError):
        evaluate.validate_queries([query(["unknown"])], {"cup-image"})


def test_live_execution_requires_opt_in_and_loopback_without_exposing_token(monkeypatch):
    args = evaluate.parse_args(["--phase", "all", "--execute"])
    monkeypatch.delenv("INSPIRATION_RETRIEVAL_EVAL", raising=False)
    with pytest.raises(evaluate.bench.BenchmarkError, match="INSPIRATION_RETRIEVAL_EVAL"):
        evaluate.validate_live_opt_in(args)
    monkeypatch.setenv("INSPIRATION_RETRIEVAL_EVAL", "1")
    monkeypatch.setenv("BENCHMARK_WISE_URL", "https://third-party.example")
    monkeypatch.setenv("BENCHMARK_WISE_TOKEN", "PRIVATE_TEST_TOKEN")
    with pytest.raises(evaluate.bench.BenchmarkError, match="loopback") as exc:
        evaluate.validate_live_opt_in(args)
    assert "PRIVATE_TEST_TOKEN" not in str(exc.value)


def test_cold_health_is_not_a_false_negative_but_identity_is_required(tmp_path, monkeypatch):
    from app.modules.library.providers.wise import WiseClient

    monkeypatch.setenv("BENCHMARK_WISE_URL", "http://127.0.0.1:9671")
    monkeypatch.setenv("BENCHMARK_WISE_TOKEN", "private-fixture-not-in-evidence")
    health = {
        "service": "inspiration-wise-bridge",
        "upstream_commit": evaluate.WISE_COMMIT,
        "worker_alive": True,
        "upstream_installation_verified": False,
        "capabilities": {"visual": {"configured": True}},
    }
    monkeypatch.setattr(WiseClient, "health", lambda self: health)
    evidence = evaluate.bench.Evidence(tmp_path, {"run_id": "a" * 32, "plan": {}})
    pipeline = evaluate.EvaluationPipeline(evidence, evaluate.parse_args([]))
    pipeline.runtime_health("health.json")
    saved = (tmp_path / "health.json").read_text()
    assert json.loads(saved)["upstream_installation_verified"] is False
    assert "private-fixture-not-in-evidence" not in saved
    for key, invalid in (
        ("service", "other"),
        ("upstream_commit", "other"),
        ("worker_alive", False),
    ):
        prior = health[key]
        health[key] = invalid
        with pytest.raises(evaluate.bench.BenchmarkError, match="not ready"):
            pipeline.runtime_health("health.json")
        health[key] = prior


def test_cleanup_remains_available_under_new_work_disk_reserve(tmp_path, monkeypatch):
    evidence = evaluate.bench.Evidence(
        tmp_path,
        {"run_id": "a" * 32, "plan": {"providers": []}, "attempts": [], "completed": []},
    )
    cleaned = []
    monkeypatch.setattr(evaluate.bench, "exclusive_run", lambda path: nullcontext())
    monkeypatch.setattr(evaluate, "validate_live_opt_in", lambda args: None)
    monkeypatch.setattr(evaluate, "load_evidence", lambda args: evidence)
    monkeypatch.setattr(evaluate.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    monkeypatch.setattr(evaluate.EvaluationPipeline, "connect", lambda self: nullcontext())
    monkeypatch.setattr(
        evaluate.EvaluationPipeline, "cleanup", lambda self, entry: cleaned.append(True)
    )
    assert evaluate.main(["--resume", "a" * 32, "--phase", "cleanup", "--execute"]) == 0
    assert cleaned == [True]


@pytest.fixture
def prepared_corpus(tmp_path, short_source):
    source_dir = tmp_path / "corpus"
    source_dir.mkdir()
    video = source_dir / "cup-video.mp4"
    video.write_bytes(short_source.read_bytes())
    for name, color in [("cup-image", "white"), ("fantasy-image", "purple")]:
        Image.new("RGB", (32, 32), color).save(source_dir / f"{name}.png")
    records = []
    for name in ("cup-image", "cup-video", "fantasy-image"):
        path = source_dir / (name + (".mp4" if name.endswith("video") else ".png"))
        records.append(
            {
                "name": name,
                "file": str(path),
                "source_hash": evaluate.bench.sha256(path),
                "bytes": path.stat().st_size,
                "mime": "video/mp4" if path.suffix == ".mp4" else "image/png",
            }
        )
    evaluate.bench.atomic_json(source_dir / "provenance.json", records)
    args = evaluate.parse_args(["--corpus-dir", str(source_dir), "--root", str(tmp_path / "runs")])
    return args, evaluate.prepare(args)


def test_prepare_only_validates_real_sources_without_indexing(prepared_corpus):
    _, evidence = prepared_corpus
    assert {s["media_type"] for s in evidence.state["samples"]} == {"image", "video"}
    assert evidence.state["completed"] == ["prepare"]
    assert not evidence.state.get("schema_initialized")
    assert len(evidence.state["queries"]) == 14
    assert "not user-approved" in evidence.state["label_status"]


def test_provenance_mismatch_rejects_before_remote_work(prepared_corpus):
    args, evidence = prepared_corpus
    path = evaluate.Path(evidence.state["samples"][0]["path"])
    path.write_bytes(b"changed")
    with pytest.raises(evaluate.bench.BenchmarkError, match="mismatch"):
        evaluate.prepare(args)


def test_real_typed_upload_originals_and_scoped_cleanup(prepared_corpus, monkeypatch):
    database = os.getenv("TEST_DATABASE_URL")
    if not database:
        pytest.skip("Set TEST_DATABASE_URL for disposable PostgreSQL integration")
    monkeypatch.setenv("BENCHMARK_DATABASE_URL", database)
    # No process run is submitted, so this plumbing test never contacts WISE.
    monkeypatch.setenv("BENCHMARK_WISE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("BENCHMARK_WISE_TOKEN", "unused-in-this-no-model-test")
    args, evidence = prepared_corpus
    pipeline = evaluate.EvaluationPipeline(evidence, args)
    with pipeline.connect():
        try:
            pipeline.upload({})
            pipeline.preview({})
            pipeline.verify_sources()
            assert evidence.state["originals_verified"]
            for source in evidence.state["samples"]:
                actual = pipeline.request("GET", f"/versions/{source['version_id']}")
                assert actual["media_type"] == source["media_type"]
        finally:
            pipeline.cleanup({})
    assert evidence.state["cleaned"] and evidence.state["schema_dropped"]
    for source in evidence.state["samples"]:
        assert evaluate.bench.sha256(source["path"]) == source["sha256"]
