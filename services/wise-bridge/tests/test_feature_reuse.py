"""Planner/isolation regressions; actual WISE storage is tested separately."""

import hashlib
import json
from pathlib import Path

import pytest

from wise_bridge import MODELS
from wise_bridge.config import Settings
from wise_bridge.runner import Canceled, EngineFailure, ProcessRunner


def payload(tmp_path, sid, kind="video", caps=("visual",)):
    path = tmp_path / f"{sid}.mp4"
    path.write_bytes(sid.encode())
    return {
        "file": str(path),
        "sha256": hashlib.sha256(sid.encode()).hexdigest(),
        "media_type": kind,
        "capabilities": list(caps),
    }


def previous(tmp_path, sources):
    folder = tmp_path / "published"
    (folder / "engine").mkdir(parents=True)
    (folder / "engine" / "original.faiss").write_bytes(b"untouched published bytes")
    caps = {cap for source in sources.values() for cap in source["capabilities"]}
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "signature": {"models": {cap: MODELS[cap] for cap in caps}},
                "sources": sources,
            }
        )
    )
    return folder


def planner(tmp_path, sources):
    runner = ProcessRunner(
        Settings(data_dir=tmp_path, token="tests-only-private-token")
    )
    calls = []

    def run(args, folder, canceled):
        if canceled():
            raise Canceled()
        if "extract-features" in args:
            listing = Path(args[args.index("--media-files-from") + 1])
            calls.append(("extract", listing.read_text().splitlines(), args))
            Path(args[args.index("--project-dir") + 1]).mkdir(
                parents=True, exist_ok=True
            )
        else:
            calls.append(("index", args))

    def operation(name, value, *args):
        calls.append((name, value))
        return {
            "counts": {sid: {"visual": 1} for sid in sources},
            "media_map": {},
            "models": {},
        }

    runner.run, runner.operation = run, operation
    return runner, calls


@pytest.mark.parametrize("deleted_kind", ["video", "image", "audio"])
def test_delete_reuses_remaining_sources_without_running_an_encoder(
    tmp_path, deleted_kind
):
    kept = payload(tmp_path, "kept")
    old = previous(
        tmp_path,
        {
            "kept": kept,
            "gone": payload(
                tmp_path,
                "gone",
                deleted_kind,
                ("audio",) if deleted_kind == "audio" else ("visual",),
            ),
        },
    )
    runner, calls = planner(tmp_path, {"kept": kept})
    snapshot = tmp_path / "new"
    runner.build({"kept": kept}, snapshot, old, lambda phase: None, lambda: False)
    assert [call[0] for call in calls] == ["prune", "index", "inspect"]
    assert calls[0][1]["retained_sources"] == ["kept"]
    assert set(calls[0][1]["expected_sources"]) == {"kept", "gone"}
    assert json.loads((snapshot / "manifest.json").read_text())["build"] == {
        "reused_sources": 1,
        "extracted_sources": 0,
    }
    assert (
        old / "engine" / "original.faiss"
    ).read_bytes() == b"untouched published bytes"


def test_mixed_add_delete_and_capability_change_extract_only_changed_sources(tmp_path):
    kept, changed = payload(tmp_path, "kept"), payload(tmp_path, "changed")
    old = previous(
        tmp_path, {"kept": kept, "changed": changed, "gone": payload(tmp_path, "gone")}
    )
    current = {
        "kept": kept,
        "changed": {**changed, "capabilities": ["visual", "audio"]},
        "image": payload(tmp_path, "image", "image"),
    }
    runner, calls = planner(tmp_path, current)
    snapshot = tmp_path / "new"
    runner.build(current, snapshot, old, lambda phase: None, lambda: False)
    assert [call[0] for call in calls] == [
        "prune",
        "extract",
        "merge",
        "index",
        "inspect",
    ]
    assert set(calls[1][1]) == {"changed.mp4", "image.mp4"}
    assert "kept.mp4" not in calls[1][1]
    assert calls[2][1]["retained_sources"] == ["kept"]
    assert set(calls[2][1]["delta_sources"]) == {"changed", "image"}
    assert "--audio-feature-id" in calls[1][2]
    assert not (snapshot / "delta").exists()


def test_reintroduced_source_uses_delta_even_with_identical_content(tmp_path):
    kept, restored = payload(tmp_path, "kept"), payload(tmp_path, "restored")
    # Latest ready deletion HEAD excludes the previous generation by contract.
    old = previous(tmp_path, {"kept": kept})
    runner, calls = planner(tmp_path, {"kept": kept, "restored": restored})
    runner.build(
        {"kept": kept, "restored": restored},
        tmp_path / "new",
        old,
        lambda phase: None,
        lambda: False,
    )
    assert next(call for call in calls if call[0] == "extract")[1] == ["restored.mp4"]


@pytest.mark.parametrize("failure", [EngineFailure("merge failed"), Canceled()])
def test_failed_or_canceled_delta_never_changes_published_snapshot(tmp_path, failure):
    kept = payload(tmp_path, "kept")
    old = previous(tmp_path, {"kept": kept})
    current = {"kept": kept, "new": payload(tmp_path, "new")}
    runner, calls = planner(tmp_path, current)
    operation = runner.operation

    def fail(name, *args):
        if name == "merge":
            raise failure
        return operation(name, *args)

    runner.operation = fail
    with pytest.raises(type(failure)):
        runner.build(current, tmp_path / "new", old, lambda phase: None, lambda: False)
    assert (
        old / "engine" / "original.faiss"
    ).read_bytes() == b"untouched published bytes"
    assert json.loads((old / "manifest.json").read_text())["sources"] == {"kept": kept}
    assert not (tmp_path / "new" / "manifest.json").exists()
    assert "inspect" not in [call[0] for call in calls]


def test_model_revision_change_cannot_reuse_old_features(tmp_path):
    kept = payload(tmp_path, "kept")
    old = previous(tmp_path, {"kept": kept})
    manifest = json.loads((old / "manifest.json").read_text())
    manifest["signature"]["models"]["visual"] = "obsolete-model"
    (old / "manifest.json").write_text(json.dumps(manifest))
    runner, calls = planner(tmp_path, {"kept": kept})
    runner.build(
        {"kept": kept},
        tmp_path / "new",
        old,
        lambda phase: None,
        lambda: False,
    )
    assert [call[0] for call in calls] == ["extract", "index", "inspect"]


def test_unavailable_optional_model_does_not_prevent_feature_only_deletion(tmp_path):
    kept = payload(tmp_path, "kept", caps=("object",))
    old = previous(tmp_path, {"kept": kept, "gone": payload(tmp_path, "gone")})
    runner, calls = planner(tmp_path, {"kept": kept})
    runner.build(
        {"kept": kept},
        tmp_path / "new",
        old,
        lambda phase: None,
        lambda: False,
        "delete",
    )
    assert [call[0] for call in calls] == ["prune", "index", "inspect"]


def test_unvalidated_optional_extraction_fails_before_copy_or_model_run(tmp_path):
    kept = payload(tmp_path, "kept")
    old = previous(tmp_path, {"kept": kept})
    sources = {"kept": kept, "new": payload(tmp_path, "new", caps=("object",))}
    runner, calls = planner(tmp_path, sources)
    with pytest.raises(EngineFailure, match="Optional model unavailable"):
        runner.build(sources, tmp_path / "new", old, lambda phase: None, lambda: False)
    assert calls == []
    assert not (tmp_path / "new" / "engine").exists()


def set_optional_fingerprint(snapshot, fingerprint):
    path = snapshot / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["signature"]["optional_models"] = {"object": fingerprint}
    path.write_text(json.dumps(manifest))


@pytest.mark.parametrize(
    "prior,current,reused",
    [("a" * 64, "a" * 64, True), ("a" * 64, "b" * 64, False), (None, "a" * 64, False)],
)
def test_optional_feature_identity_controls_upsert_reuse(
    tmp_path, monkeypatch, prior, current, reused
):
    sources = {"source": payload(tmp_path, "source", caps=("object",))}
    old = previous(tmp_path, sources)
    set_optional_fingerprint(old, prior)
    monkeypatch.setattr(
        "wise_bridge.runner.inspect_optional",
        lambda *args: {"manifest_digest": "c" * 64, "feature_fingerprint": current},
    )
    # This test covers planner storage identity, not production model readiness.
    monkeypatch.setattr("wise_bridge.runner.require_optional", lambda *args: None)
    runner, calls = planner(tmp_path, sources)
    new = tmp_path / "new"
    runner.build(sources, new, old, lambda phase: None, lambda: False)
    assert ("extract" not in [call[0] for call in calls]) is reused
    if not reused:
        command = next(call[2] for call in calls if call[0] == "extract")
        assert command[:2] == ["-m", "wise_bridge.offline_entry"]
    assert json.loads((new / "manifest.json").read_text())["signature"][
        "optional_models"
    ] == {"object": current}


@pytest.mark.parametrize("fingerprint", [None, "a" * 64])
def test_delete_preserves_historical_fingerprint_without_reading_model_files(
    tmp_path, monkeypatch, fingerprint
):
    kept = payload(tmp_path, "kept", caps=("object",))
    old = previous(tmp_path, {"kept": kept, "gone": payload(tmp_path, "gone")})
    set_optional_fingerprint(old, fingerprint)

    def no_reads(*args):
        pytest.fail("Deleting feature rows must not read model files")

    monkeypatch.setattr("wise_bridge.runner.inspect_optional", no_reads)
    runner, calls = planner(tmp_path, {"kept": kept})
    new = tmp_path / "new"
    runner.build({"kept": kept}, new, old, lambda phase: None, lambda: False, "delete")
    assert [call[0] for call in calls] == ["prune", "index", "inspect"]
    assert json.loads((new / "manifest.json").read_text())["signature"][
        "optional_models"
    ] == {"object": fingerprint}
    # Replayed cleanup with the same source set still must not consult weights.
    runner.build(
        {"kept": kept},
        tmp_path / "again",
        new,
        lambda phase: None,
        lambda: False,
        "delete",
    )


def test_changed_optional_manifest_during_build_never_publishes_signature(
    tmp_path, monkeypatch
):
    sources = {"source": payload(tmp_path, "source", caps=("object",))}
    old = previous(tmp_path, sources)
    set_optional_fingerprint(old, "a" * 64)
    fingerprints = iter(["a" * 64, "b" * 64])
    monkeypatch.setattr(
        "wise_bridge.runner.inspect_optional",
        lambda *args: {
            "manifest_digest": "c" * 64,
            "feature_fingerprint": next(fingerprints),
        },
    )
    runner, _calls = planner(tmp_path, sources)
    with pytest.raises(EngineFailure, match="fingerprint changed"):
        runner.build(sources, tmp_path / "new", old, lambda phase: None, lambda: False)
    assert not (tmp_path / "new" / "manifest.json").exists()
    assert json.loads((old / "manifest.json").read_text())["signature"][
        "optional_models"
    ] == {"object": "a" * 64}


def test_delete_with_missing_published_engine_never_falls_back_to_inference(tmp_path):
    kept = payload(tmp_path, "kept")
    old = previous(tmp_path, {"kept": kept})
    (old / "engine" / "original.faiss").unlink()
    (old / "engine").rmdir()
    runner, calls = planner(tmp_path, {"kept": kept})
    with pytest.raises(EngineFailure, match="published feature snapshot"):
        runner.build(
            {"kept": kept},
            tmp_path / "new",
            old,
            lambda phase: None,
            lambda: False,
            "delete",
        )
    assert calls == []
