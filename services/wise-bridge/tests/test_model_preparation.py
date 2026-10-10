"""Cache integrity unit tests only; no model weights or inference are involved."""

import hashlib
import importlib.util
from pathlib import Path

import pytest


def load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "prepare_audio_model.py"
    spec = importlib.util.spec_from_file_location("audio_preparation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_corrupt_dependency_does_not_publish_any_model_refs(tmp_path):
    prepare = load_script().prepare
    data = b"verified test payload"
    manifest = {"models": []}
    paths = {}
    for index, repo in enumerate(("microsoft/msclap", "gpt2")):
        root = tmp_path / ("models--" + repo.replace("/", "--"))
        path = root / "snapshots" / "test-revision" / "weights"
        path.parent.mkdir(parents=True)
        path.write_bytes(data if index == 0 else b"x" * len(data))
        refs = root / "refs"
        refs.mkdir()
        (refs / "main").write_text("previous-good-revision")
        paths[repo] = path
        manifest["models"].append(
            {
                "repository": repo,
                "revision": "test-revision",
                "files": [
                    {
                        "filename": "weights",
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                ],
            }
        )

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        prepare(manifest, lambda **kwargs: paths[kwargs["repo_id"]])
    for path in paths.values():
        assert (
            path.parents[2] / "refs" / "main"
        ).read_text() == "previous-good-revision"


def test_verify_only_never_downloads_or_changes_refs(tmp_path):
    prepare = load_script().prepare
    data = b"complete artifact"
    root = tmp_path / "models--gpt2"
    path = root / "snapshots" / "locked-revision" / "weights"
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    refs = root / "refs"
    refs.mkdir()
    (refs / "main").write_text("another-revision")

    def offline_download(**kwargs):
        assert kwargs["local_files_only"] is True
        return path

    manifest = {
        "models": [
            {
                "repository": "gpt2",
                "revision": "locked-revision",
                "files": [
                    {
                        "filename": "weights",
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    },
                ],
            }
        ]
    }
    with pytest.raises(RuntimeError, match="cache ref"):
        prepare(manifest, offline_download, verify_only=True)
    assert (refs / "main").read_text() == "another-revision"
