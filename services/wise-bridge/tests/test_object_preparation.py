"""Only local byte fixtures; no weights downloaded during tests."""

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_object_model.py"
spec = importlib.util.spec_from_file_location("object_preparation", SCRIPT)
preparation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preparation)


def test_default_prints_exact_public_lock_without_importing_downloader(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    lock = json.loads(result.stdout)
    assert lock["revision"] == "cfd3195ba4ea9592eec887ded089f4c08eff231d"
    assert (
        lock["total_bytes"] == sum(row["bytes"] for row in lock["files"]) == 621505532
    )
    assert len(lock["files"]) == 8
    assert not any(row["name"].endswith(".bin") for row in lock["files"])
    assert not list(tmp_path.iterdir())


def test_verify_cache_is_offline_and_rejects_corrupt_weights(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    revision = "a" * 40
    repo = tmp_path / "models--google--owlv2-base-patch16-ensemble"
    snapshot = repo / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text(revision)
    raw = b"small explicit fixture"
    file = snapshot / "model.safetensors"
    file.write_bytes(raw)
    entry = {
        "name": file.name,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    lock = {"files": [entry], "revision": revision}
    monkeypatch.setattr(preparation.importlib.metadata, "version", lambda _: "fixture")
    assert preparation.prepare(lock, download=False)["torch"] == "fixture"
    file.write_bytes(b"x" * len(raw))
    with pytest.raises(ValueError, match="SHA-256"):
        preparation.prepare(lock, download=False)


def test_no_license_record_is_written_without_operator_purpose(tmp_path):
    target = tmp_path / "object.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--write-manifest", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0 and not target.exists()
