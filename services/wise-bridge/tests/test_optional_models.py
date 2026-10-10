"""No real weights: fail-closed local manifests, cache invalidation, and no network."""

import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from wise_bridge import MODELS, WISE_COMMIT
from wise_bridge import optional_models as optional
from wise_bridge.api import create_app
from wise_bridge.config import Settings


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    for name in (
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "PYTORCH_TRANSFORMERS_CACHE",
        "PYTORCH_PRETRAINED_BERT_CACHE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv(
        "FEATURE_EXTRACTOR_CONFIG",
        '{"default":{"compile":false,"warmup":false},"transformers/owlv2/google/owlv2-base-patch16-ensemble":{"device":"cpu"}}',
    )
    monkeypatch.setattr(optional.importlib.metadata, "version", lambda _: "fixture-1")
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    settings = Settings(
        data_dir=tmp_path / "data",
        token="private-test-service-token-0000",
        enabled_capabilities=("visual", "audio", "face", "object"),
        worker_enabled=False,
        min_free_bytes=0,
        optional_manifest_dir=manifests,
        optional_use="noncommercial_research",
    )
    roots = {}
    for cap in ("face", "object"):
        revision = "a" * 40
        root = (
            Path.home() / ".insightface" / "models" / "buffalo_l"
            if cap == "face"
            else tmp_path
            / "hf"
            / "hub"
            / "models--google--owlv2-base-patch16-ensemble"
            / "snapshots"
            / revision
        )
        root.mkdir(parents=True)
        if cap == "object":
            refs = root.parent.parent / "refs"
            refs.mkdir()
            (refs / "main").write_text(revision)
        files = []
        for name in sorted(
            optional.FACE_REQUIRED if cap == "face" else optional.OBJECT_REQUIRED
        ):
            data = f"Explicit fake weight fixture: {name}".encode()
            (root / name).write_bytes(data)
            files.append(
                {
                    "name": name,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        manifest = {
            "schema_version": 1,
            "capability": cap,
            "feature_id": MODELS[cap],
            "upstream_commit": WISE_COMMIT,
            "revision": revision,
            "dependencies": {key: "fixture-1" for key in optional.DEPENDENCIES[cap]},
            "license_record": {
                "purpose": "noncommercial_research",
                "reference": "Test only",
                "license": "InsightFace-NonCommercial-Research"
                if cap == "face"
                else "Apache-2.0",
            },
            "files": files,
            "ready": True,
            "inference_verified": True,
        }
        (manifests / f"{cap}.json").write_text(json.dumps(manifest))
        roots[cap] = root
    optional._digest.cache_clear()
    return settings, roots


@pytest.mark.parametrize("cap", ["face", "object"])
def test_complete_integrity_and_forged_validation_flag_never_claim_inference_ready(
    prepared, cap
):
    settings, _ = prepared
    state = optional.inspect_optional(cap, settings)
    assert state["integrity_verified"] is True and state["ready"] is False
    assert state["reason"] == "model_validation_required"
    with pytest.raises(
        optional.OptionalModelUnavailable, match="model_validation_required"
    ):
        optional.require_optional([cap], settings)
    assert "reference" not in state and "files" not in state


def test_disabled_check_does_not_touch_files_or_network(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(
        optional, "_read_json", lambda _: pytest.fail("Must not read a manifest")
    )
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("No network"))
    settings = Settings(optional_manifest_dir=tmp_path)
    assert (
        optional.inspect_optional("face", settings)["reason"] == "capability_disabled"
    )


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("missing_manifest", "model_manifest_missing"),
        ("wrong_commit", "model_manifest_invalid"),
        ("license", "license_record_missing"),
        ("purpose", "license_scope_mismatch"),
        ("dependency", "dependency_mismatch"),
        ("missing_file", "model_file_missing"),
        ("hash", "model_hash_mismatch"),
        ("revision", "model_revision_mismatch"),
        ("traversal", "model_manifest_invalid"),
    ],
)
def test_incomplete_or_changed_installations_explain_why_they_are_unavailable(
    prepared, mutation, reason
):
    settings, roots = prepared
    path = settings.optional_manifest_dir / "object.json"
    data = json.loads(path.read_text())
    if mutation == "missing_manifest":
        path.unlink()
    elif mutation == "missing_file":
        (roots["object"] / "model.safetensors").unlink()
    elif mutation == "hash":
        (roots["object"] / "model.safetensors").write_bytes(b"changed")
    elif mutation == "revision":
        (roots["object"].parent.parent / "refs" / "main").write_text("b" * 40)
    else:
        if mutation == "wrong_commit":
            data["upstream_commit"] = "wrong"
        elif mutation == "license":
            data.pop("license_record")
        elif mutation == "purpose":
            data["license_record"]["license"] = "not reviewed"
        elif mutation == "dependency":
            data["dependencies"]["torch"] = "wrong-version"
        elif mutation == "traversal":
            data["files"][0]["name"] = "../../outside"
        path.write_text(json.dumps(data))
    state = optional.inspect_optional("object", settings)
    assert state["reason"] == reason and state["ready"] is False


def test_file_digest_cache_rechecks_same_size_replacement_even_with_restored_mtime(
    prepared,
):
    settings, roots = prepared
    assert optional.inspect_optional("object", settings)["integrity_verified"]
    initial = optional._digest.cache_info()
    assert optional.inspect_optional("object", settings)["integrity_verified"]
    assert optional._digest.cache_info().hits > initial.hits
    path = roots["object"] / "model.safetensors"
    before = path.stat()
    replacement = roots["object"] / "replacement"
    replacement.write_bytes(b"x" * before.st_size)
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    replacement.replace(path)
    assert (
        optional.inspect_optional("object", settings)["reason"] == "model_hash_mismatch"
    )


def test_manifest_change_invalidates_readiness_and_extra_face_files_are_rejected(
    prepared,
):
    settings, roots = prepared
    assert optional.inspect_optional("face", settings)["integrity_verified"]
    (roots["face"] / "unexpected.onnx").write_bytes(b"unreviewed model")
    assert (
        optional.inspect_optional("face", settings)["reason"] == "model_layout_mismatch"
    )
    path = settings.optional_manifest_dir / "object.json"
    data = json.loads(path.read_text())
    data["files"][0]["sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    assert (
        optional.inspect_optional("object", settings)["reason"] == "model_hash_mismatch"
    )


def test_legacy_transformer_override_cannot_make_preflight_check_a_different_cache(
    prepared, monkeypatch
):
    settings, _ = prepared
    monkeypatch.setenv("TRANSFORMERS_CACHE", "some-other-cache")
    assert (
        optional.inspect_optional("object", settings)["reason"]
        == "model_layout_mismatch"
    )


def test_api_health_reports_unvalidated_and_submission_does_not_create_job(prepared):
    settings, _ = prepared
    app = create_app(settings)
    with TestClient(
        app, headers={"Authorization": "Bearer " + settings.token}
    ) as client:
        health = client.get("/health").json()
        assert health["capabilities"]["object"]["reason"] == "model_validation_required"
        project, source, job = (str(uuid.uuid4()) for _ in range(3))
        response = client.post(
            f"/v1/projects/{project}/sources/{source}",
            data={"job_id": job, "media_type": "image", "capabilities": '["object"]'},
            files={"file": ("preview.png", b"unused fixture")},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "model_validation_required"
        assert app.state.store.job(job) is None
        response = client.post(
            f"/v1/projects/{project}/search",
            json={"query": "cup", "modality": "object"},
        )
        assert response.status_code == 422
        assert (
            "fixture-1" not in response.text
            and str(settings.optional_manifest_dir) not in response.text
        )


def test_network_guard_is_child_only_and_blocks_python_download_transport():
    # The fake acceptance removes only the gate for this isolated guard test.
    # No real model is loaded, socket request is stopped by the audit event.
    script = """
import socket
from wise_bridge import optional_models as m
m.require_optional = lambda _: None
m.enforce_optional_runtime(['face'])
try:
    socket.socket().connect(('127.0.0.1', 9))
except OSError as exc:
    assert str(exc) == 'Network is disabled in optional model inference'
else:
    raise AssertionError('Network was not blocked')
print('guard verified')
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "guard verified"
    assert optional._network_guard_installed is False


@pytest.mark.parametrize("joined", [False, True])
def test_optional_cli_cannot_bypass_gate_by_mislabeling_capabilities(tmp_path, joined):
    arguments = (
        ["--image-feature-id=" + MODELS["face"]]
        if joined
        else ["--image-feature-id", MODELS["face"]]
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "WISE_BRIDGE_CAPABILITIES": "face",
        "WISE_OPTIONAL_MANIFEST_DIR": str(tmp_path),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "wise_bridge.offline_entry",
            "[]",
            "--",
            "extract-features",
            *arguments,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "face/model_manifest_missing" in result.stderr
    assert "No module named wise" not in result.stderr
