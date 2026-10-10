"""Read-only optional-model preflight. This module never imports model libraries.

Integrity and a recorded license review are NOT inference acceptance. An explicit
offline probe must also produce a valid local attestation for the exact artifacts.
No editable manifest flag can enable an optional model in the running service.
"""

import hashlib
import importlib.metadata
import json
import os
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from wise_bridge import MODELS, WISE_COMMIT

OPTIONAL = frozenset({"face", "object"})
FACE_REQUIRED = {"det_10g.onnx", "w600k_r50.onnx", "genderage.onnx"}
FACE_ALLOWED = FACE_REQUIRED | {"1k3d68.onnx", "2d106det.onnx"}
OBJECT_REQUIRED = {
    "config.json",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.json",
    "merges.txt",
    "model.safetensors",
}
OBJECT_ALLOWED = OBJECT_REQUIRED | {"tokenizer.json", "added_tokens.json"}
DEPENDENCIES = {
    "face": {
        "insightface",
        "onnxruntime",
        "opencv-python",
        "torch",
        "numpy",
        "sqlalchemy",
    },
    "object": {"transformers", "torch", "torchvision", "numpy", "sqlalchemy"},
}
LICENSES = {
    "face": "https://www.insightface.ai/",
    "object": "https://huggingface.co/google/owlv2-base-patch16-ensemble",
}


class OptionalModelUnavailable(RuntimeError):
    def __init__(self, capability, reason):
        self.capability, self.reason = capability, reason
        # Only stable codes cross process/HTTP boundaries, never paths or manifests.
        super().__init__(f"Optional model unavailable: {capability}/{reason}")


def _reject(capability, reason):
    raise OptionalModelUnavailable(capability, reason)


def _identity(path):
    value = path.stat()
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


@lru_cache(maxsize=128)
def _digest(path_string, identity):
    path = Path(path_string)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        value = os.fstat(stream.fileno())
        opened = (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        # Windows path.stat and fstat expose different ctime semantics. Compare
        # the common identity here; path.stat's full tuple still guards both the
        # cache key and the post-read check, including ctime-based replacement.
        if opened[:4] != identity[:4]:
            raise OSError("Model file changed")
        while chunk := stream.read(1024**2):
            digest.update(chunk)
    if _identity(path) != identity:
        raise OSError("Model file changed")
    return digest.hexdigest()


def _read_json(path):
    if path.stat().st_size > 128 * 1024:
        raise ValueError("Manifest exceeds limit")
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError("Manifest is not an object")
    return data, hashlib.sha256(raw).hexdigest()


def _model_root(capability, manifest):
    if capability == "face":
        root = Path.home() / ".insightface" / "models" / "buffalo_l"
        # Only the audited ordinary buffalo_l package is supported. Package
        # descriptors can load additional files and are deliberately not accepted.
        if root.exists() and any(path.suffix != ".onnx" for path in root.iterdir()):
            _reject(capability, "model_layout_mismatch")
        return root, root
    revision = manifest.get("revision", "")
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
        _reject(capability, "model_manifest_invalid")
    if any(
        os.getenv(name)
        for name in (
            "TRANSFORMERS_CACHE",
            "PYTORCH_TRANSFORMERS_CACHE",
            "PYTORCH_PRETRAINED_BERT_CACHE",
        )
    ):
        _reject(capability, "model_layout_mismatch")
    default_hub = (
        Path(os.getenv("HF_HOME", str(Path.home() / ".cache" / "huggingface"))) / "hub"
    )
    hub = Path(
        os.getenv("HF_HUB_CACHE", os.getenv("HUGGINGFACE_HUB_CACHE", str(default_hub)))
    )
    repo = hub / "models--google--owlv2-base-patch16-ensemble"
    ref = repo / "refs" / "main"
    if (
        not ref.is_file()
        or ref.stat().st_size > 100
        or ref.read_text().strip() != revision
    ):
        _reject(capability, "model_revision_mismatch")
    return repo / "snapshots" / revision, repo


def inspect_optional(capability, settings=None):
    """Safe public state; only a verified probe can promote integrity to ready."""
    from wise_bridge.config import Settings

    settings = settings or Settings()
    state = {
        "configured": capability in settings.enabled_capabilities,
        "ready": False,
        "model_id": MODELS[capability],
        "license_url": LICENSES[capability],
        "availability": "disabled",
        "reason": "capability_disabled",
    }
    if not state["configured"]:
        return state
    try:
        path = settings.optional_manifest_dir / f"{capability}.json"
        if not path.is_file():
            _reject(capability, "model_manifest_missing")
        manifest, manifest_digest = _read_json(path)
        if (
            manifest.get("schema_version") != 1
            or manifest.get("capability") != capability
            or manifest.get("feature_id") != MODELS[capability]
            or manifest.get("upstream_commit") != WISE_COMMIT
        ):
            _reject(capability, "model_manifest_invalid")
        allowed_uses = {"commercial", "noncommercial_research"}
        if capability == "object":
            allowed_uses.add("development_evaluation")
        if settings.optional_use not in allowed_uses:
            _reject(capability, "license_use_unspecified")
        license_record = manifest.get("license_record")
        if (
            not isinstance(license_record, dict)
            or license_record.get("purpose") != settings.optional_use
            or not isinstance(license_record.get("reference"), str)
            or not license_record["reference"].strip()
        ):
            _reject(capability, "license_record_missing")
        expected_license = (
            "Apache-2.0"
            if capability == "object"
            else (
                "InsightFace-NonCommercial-Research"
                if settings.optional_use == "noncommercial_research"
                else "Separately-Granted-Commercial-License"
            )
        )
        if license_record.get("license") != expected_license:
            _reject(capability, "license_scope_mismatch")
        # This records an operator's review reference. It neither establishes
        # ownership nor grants permission; it cannot make this state ready.
        if (
            os.getenv("HF_HUB_OFFLINE") != "1"
            or os.getenv("TRANSFORMERS_OFFLINE") != "1"
        ):
            _reject(capability, "offline_mode_required")
        dependencies = manifest.get("dependencies")
        if (
            not isinstance(dependencies, dict)
            or set(dependencies) != DEPENDENCIES[capability]
        ):
            _reject(capability, "dependency_manifest_invalid")
        for package, expected in dependencies.items():
            if (
                not isinstance(expected, str)
                or importlib.metadata.version(package) != expected
            ):
                _reject(capability, "dependency_mismatch")
        files = manifest.get("files")
        if not isinstance(files, list) or not 1 <= len(files) <= 16:
            _reject(capability, "model_manifest_invalid")
        required = FACE_REQUIRED if capability == "face" else OBJECT_REQUIRED
        allowed = FACE_ALLOWED if capability == "face" else OBJECT_ALLOWED
        names = [entry.get("name") for entry in files if isinstance(entry, dict)]
        if (
            len(names) != len(files)
            or len(set(names)) != len(names)
            or any(not isinstance(name, str) for name in names)
            or not required <= set(names) <= allowed
        ):
            _reject(capability, "model_manifest_invalid")
        root, boundary = _model_root(capability, manifest)
        if root.is_dir() and {p.name for p in root.iterdir()} - set(names):
            _reject(capability, "model_layout_mismatch")
        for entry in files:
            expected = entry.get("sha256")
            size = entry.get("bytes")
            if (
                not isinstance(expected, str)
                or not re.fullmatch(r"[a-f0-9]{64}", expected)
                or isinstance(size, bool)
                or not isinstance(size, int)
                or not 0 < size < 4 * 1024**3
            ):
                _reject(capability, "model_manifest_invalid")
            file = (root / entry["name"]).resolve()
            if not file.is_relative_to(boundary.resolve()):
                _reject(capability, "model_layout_mismatch")
            if not file.is_file():
                _reject(capability, "model_file_missing")
            identity = _identity(file)
            if identity[2] != size or _digest(str(file), identity) != expected:
                _reject(capability, "model_hash_mismatch")
        from wise_bridge.optional_attestation import canonical, runtime_identity

        runtime = runtime_identity()
        if capability == "object":
            from wise_bridge.optional_runtime import check_device_configuration

            check_device_configuration(runtime["extractor_config"])
        state.update(
            {
                "availability": "validation_required",
                "reason": "model_validation_required",
                "integrity_verified": True,
                "manifest_digest": manifest_digest,
                "feature_fingerprint": hashlib.sha256(
                    canonical(
                        {
                            "manifest_digest": manifest_digest,
                            "runtime": runtime,
                        }
                    )
                ).hexdigest(),
            }
        )
        if capability == "object":
            from wise_bridge.optional_attestation import verified_receipt

            receipt = verified_receipt(settings, capability, manifest_digest)
            if receipt:
                state.update(
                    availability="ready",
                    reason=None,
                    ready=True,
                    validation=receipt,
                )
    except OptionalModelUnavailable as exc:
        state.update(availability="unavailable", reason=exc.reason)
    except importlib.metadata.PackageNotFoundError:
        state.update(availability="unavailable", reason="dependency_missing")
    except (OSError, ValueError, TypeError):
        state.update(availability="unavailable", reason="model_manifest_invalid")
    return state


def require_optional(capabilities, settings=None):
    for capability in sorted(set(capabilities) & OPTIONAL):
        state = inspect_optional(capability, settings)
        if not state["ready"]:
            _reject(capability, state["reason"])


_network_guard_installed = False
_probe_scope = ContextVar("optional_probe_scope", default=None)


def _deny_model_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise OSError("Network is disabled in optional model inference")


def _install_network_guard():
    global _network_guard_installed
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["NO_ALBUMENTATIONS_UPDATE"] = "1"
    if not _network_guard_installed:
        sys.addaudithook(_deny_model_network)
        _network_guard_installed = True


def enforce_optional_runtime(capabilities, expected_fingerprint=None):
    """Call ONLY inside the optional inference child, never the HTTP service."""
    requested = OPTIONAL.intersection(capabilities)
    if not requested:
        return
    scope = _probe_scope.get() or {}
    remaining = requested - set(scope)
    if remaining:
        require_optional(remaining)
    if expected_fingerprint is not None:
        for capability in requested:
            state = inspect_optional(capability, scope.get(capability))
            if (
                not state.get("integrity_verified")
                or state.get("feature_fingerprint") != expected_fingerprint
            ):
                _reject(capability, "model_fingerprint_mismatch")
    _install_network_guard()
    if "object" in requested:
        from wise_bridge.optional_runtime import install_object_runtime

        install_object_runtime()


@contextmanager
def explicit_probe_scope(settings, capability):
    """Private CLI child scope, never enabled by HTTP input or environment flags.

    The probe must test the same production search function before it has a
    receipt. Scope is process-local, bounded to integrity-checked OWLv2, and still
    installs the network guard. Calling this in a HTTP server is unsupported.
    """
    if capability != "object":
        _reject(capability, "model_validation_required")
    state = inspect_optional(capability, settings)
    if not state.get("integrity_verified"):
        _reject(capability, state["reason"])
    _install_network_guard()
    from wise_bridge.optional_runtime import install_object_runtime

    install_object_runtime()
    token = _probe_scope.set({capability: settings})
    try:
        yield state["manifest_digest"]
    finally:
        _probe_scope.reset(token)
