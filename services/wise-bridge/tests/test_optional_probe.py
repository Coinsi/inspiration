"""Receipt/probe contracts use fake weights; these are NOT real inference tests."""

import hashlib
import json
import os

import pytest
import test_optional_models
from wise_bridge import optional_attestation as attestation
from wise_bridge import optional_models, optional_probe
from wise_bridge.optional_probe_worker import validate_rows


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    return test_optional_models.prepared.__wrapped__(tmp_path, monkeypatch)


def evidence(settings):
    state = optional_models.inspect_optional("object", settings)
    return {
        "protocol": attestation.PROTOCOL,
        "capability": "object",
        "nonce": "c" * 64,
        "manifest_digest": state["manifest_digest"],
        "runtime": attestation.runtime_identity(),
        "checks": dict.fromkeys(attestation.CHECKS, True),
        "outputs": {
            "image_vectors": 2,
            "video_vectors": 4,
            "video_frames": 8,
            "text_results": 6,
            "region_results": 3,
            "image_sha256": "1" * 64,
            "video_sha256": "2" * 64,
            "results_sha256": "3" * 64,
            "device": "cpu",
        },
    }


def sign(settings, value):
    attestation.record_probe(
        settings,
        value,
        manifest_digest=value["manifest_digest"],
        runtime=value["runtime"],
        nonce=value["nonce"],
    )


def test_signed_receipt_can_accept_exact_object_installation_only(prepared):
    settings, _ = prepared
    value = evidence(settings)
    sign(settings, value)
    state = optional_models.inspect_optional("object", settings)
    assert state["ready"] is True and state["availability"] == "ready"
    assert state["validation"]["protocol"] == attestation.PROTOCOL
    assert optional_models.inspect_optional("face", settings)["ready"] is False
    optional_models.require_optional(["object"], settings)
    assert "key" not in str(state) and "outputs" not in state


@pytest.mark.parametrize(
    "mutation", ["signature", "output", "manifest", "dependency", "runtime", "key"]
)
def test_mutated_receipt_or_installation_is_not_ready(prepared, monkeypatch, mutation):
    settings, _ = prepared
    value = evidence(settings)
    sign(settings, value)
    path = attestation._receipt_path(settings, "object")
    record = json.loads(path.read_text())
    if mutation == "signature":
        record["signature"] = "0" * 64
        path.write_text(json.dumps(record))
    elif mutation == "output":
        record["payload"]["evidence"]["outputs"]["image_vectors"] = 100
        path.write_text(json.dumps(record))
    elif mutation == "manifest":
        manifest = settings.optional_manifest_dir / "object.json"
        manifest.write_text(manifest.read_text() + "\n")
    elif mutation == "dependency":
        monkeypatch.setattr(
            optional_models.importlib.metadata, "version", lambda _: "new-version"
        )
    elif mutation == "runtime":
        monkeypatch.setenv("FEATURE_EXTRACTOR_CONFIG", '{"default":{"device":"cpu"}}')
    else:
        attestation._key_path(settings).write_bytes(b"z" * 32)
    assert optional_models.inspect_optional("object", settings)["ready"] is False


def test_replacement_weights_plus_updated_manifest_requires_new_probe(prepared):
    settings, roots = prepared
    sign(settings, evidence(settings))
    file = roots["object"] / "model.safetensors"
    raw = b"r" * file.stat().st_size
    file.write_bytes(raw)
    manifest_path = settings.optional_manifest_dir / "object.json"
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["files"]:
        if entry["name"] == file.name:
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    state = optional_models.inspect_optional("object", settings)
    assert state["integrity_verified"] and not state["ready"]


@pytest.mark.parametrize(
    "name", ["added_tokens.json", "tokenizer.json", "pytorch_model.bin"]
)
def test_unlisted_loader_files_invalidate_an_existing_receipt(prepared, name):
    settings, roots = prepared
    sign(settings, evidence(settings))
    (roots["object"] / name).write_bytes(b"unlisted loader input")
    state = optional_models.inspect_optional("object", settings)
    assert state["reason"] == "model_layout_mismatch" and not state["ready"]


def test_object_development_purpose_is_explicit_and_does_not_license_face(prepared):
    from dataclasses import replace

    settings, _ = prepared
    settings = replace(settings, optional_use="development_evaluation")
    manifest_path = settings.optional_manifest_dir / "object.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["license_record"]["purpose"] = "development_evaluation"
    manifest_path.write_text(json.dumps(manifest))
    assert optional_models.inspect_optional("object", settings)["integrity_verified"]
    assert (
        optional_models.inspect_optional("face", settings)["reason"]
        == "license_use_unspecified"
    )


def test_model_fingerprint_includes_effective_configuration(prepared, monkeypatch):
    settings, _ = prepared
    before = optional_models.inspect_optional("object", settings)
    monkeypatch.setenv(
        "FEATURE_EXTRACTOR_CONFIG", '{"default":{"device":"cpu","compile":false}}'
    )
    after = optional_models.inspect_optional("object", settings)
    assert after["manifest_digest"] == before["manifest_digest"]
    assert after["feature_fingerprint"] != before["feature_fingerprint"]


def test_probe_scope_still_requires_current_fingerprint(prepared, monkeypatch):
    from wise_bridge import optional_runtime

    settings, _ = prepared
    monkeypatch.setattr(optional_models, "_install_network_guard", lambda: None)
    monkeypatch.setattr(optional_runtime, "install_object_runtime", lambda: None)
    current = optional_models.inspect_optional("object", settings)[
        "feature_fingerprint"
    ]
    with optional_models.explicit_probe_scope(settings, "object"):
        optional_models.enforce_optional_runtime(
            ["object"], expected_fingerprint=current
        )
        with pytest.raises(
            optional_models.OptionalModelUnavailable, match="model_fingerprint_mismatch"
        ):
            optional_models.enforce_optional_runtime(
                ["object"], expected_fingerprint="old"
            )


def test_forged_unsigned_json_and_failed_checks_are_not_accepted(prepared):
    settings, _ = prepared
    value = evidence(settings)
    path = attestation._receipt_path(settings, "object")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"ready": True, "evidence": value}))
    assert not optional_models.inspect_optional("object", settings)["ready"]
    value["checks"]["region_query"] = False
    with pytest.raises(ValueError):
        sign(settings, value)
    assert not attestation._key_path(settings).exists()


def test_driver_runs_each_child_before_signing_and_cleans_owned_fixtures(
    prepared, monkeypatch, tmp_path
):
    settings, _ = prepared
    image = tmp_path / "operator.png"
    image.write_bytes(b"explicit test double; no actual image/model inference")
    steps = []

    def run(_runner, args, directory, _canceled, timeout):
        stage = args[2]
        steps.append(stage)
        assert timeout <= 1800
        if stage == "prepare":
            (directory / "source").mkdir()
            (directory / "source" / "owned.png").write_bytes(b"owned")
            (directory / "engine").mkdir()
        if stage == "query":
            request = json.loads((directory / "request.json").read_text())
            result = evidence(settings)
            result.update(
                {key: request[key] for key in ("nonce", "manifest_digest", "runtime")}
            )
            (directory / "evidence.json").write_text(json.dumps(result))

    monkeypatch.setattr(optional_probe.ProcessRunner, "run", run)
    result = optional_probe.run_probe(image, settings)
    assert steps == ["prepare", "extract", "index", "query"] and result["ready"]
    assert not list(settings.data_dir.rglob("input.image"))
    assert not list(settings.data_dir.rglob("source"))
    assert image.is_file()


def test_failed_child_cannot_record_readiness(prepared, monkeypatch, tmp_path):
    settings, _ = prepared
    image = tmp_path / "test.png"
    image.write_bytes(b"fixture")

    def fail(*args, **kwargs):
        raise RuntimeError("Deliberate inference failure")

    monkeypatch.setattr(optional_probe.ProcessRunner, "run", fail)
    with pytest.raises(RuntimeError, match="Deliberate"):
        optional_probe.run_probe(image, settings)
    assert not attestation._key_path(settings).exists()
    assert not optional_models.inspect_optional("object", settings)["ready"]


def row(vector="1", source="image"):
    return {
        "source_id": source,
        "vector_id": vector,
        "modality": "object",
        "score": 0.1,
        "bbox": {"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.3},
        "start_seconds": None,
        "end_seconds": None,
    }


def test_same_frame_regions_remain_distinct_and_invalid_metadata_fails():
    assert len(validate_rows([row("1"), row("2")], {"image"}, "image", "video")) == 2
    for invalid in (
        {**row(), "bbox": {"x": float("nan"), "y": 0, "w": 0.2, "h": 0.3}},
        {**row(), "bbox": {"x": 0.9, "y": 0, "w": 0.2, "h": 0.3}},
        {**row(), "start_seconds": 0},
        {**row(), "source_id": "another-project"},
        {**row(), "vector_id": "missing"},
        {**row(), "score": float("inf")},
    ):
        with pytest.raises(ValueError):
            validate_rows([invalid], {"image"}, "image", "video")


@pytest.mark.skipif(os.name == "nt", reason="Unix service key permission check")
def test_world_readable_receipt_key_is_rejected(prepared):
    settings, _ = prepared
    sign(settings, evidence(settings))
    os.chmod(attestation._key_path(settings), 0o644)
    assert not optional_models.inspect_optional("object", settings)["ready"]
