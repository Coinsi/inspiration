"""Local probe receipts. This is a service-owner boundary, not remote attestation.

Only the CLI driver signs the output of its freshly launched inference child.
HTTP code only verifies. A privileged owner who can replace code/read this key
can also replace the running service and is outside this trust boundary.
"""

import hashlib
import hmac
import json
import os
import platform
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from wise_bridge import WISE_COMMIT

PROTOCOL = "wise-optional-probe-v1"
CHECKS = frozenset(
    {
        "pinned_upstream",
        "offline_inference",
        "image_and_video_vectors",
        "faiss_metadata_consistent",
        "text_query",
        "region_query",
        "source_scope",
        "finite_bounded_regions",
        "image_null_time",
        "video_time",
        "stable_vector_ids",
        "empty_scope",
    }
)
IMPLEMENTATION_FILES = (
    "optional_models.py",
    "optional_attestation.py",
    "optional_probe.py",
    "optional_probe_worker.py",
    "optional_runtime.py",
    "object_query.py",
    "query_errors.py",
    "query_process.py",
    "offline_entry.py",
    "upstream_worker.py",
    "upstream_compat.py",
    "search_cache.py",
    "runner.py",
    "config.py",
)
DEFAULT_EXTRACTOR_CONFIG = {"default": {"compile": False, "warmup": False}}


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def runtime_identity():
    # File hashes share the same stat-keyed bounded cache as model verification;
    # no ML import or repeated model-sized hashing happens in the HTTP process.
    from wise_bridge.optional_models import _digest, _identity

    root = Path(__file__).parent
    files = {
        name: _digest(str(root / name), _identity(root / name))
        for name in IMPLEMENTATION_FILES
    }
    config = json.loads(
        os.getenv("FEATURE_EXTRACTOR_CONFIG", json.dumps(DEFAULT_EXTRACTOR_CONFIG))
    )
    return {
        "implementation": hashlib.sha256(canonical(files)).hexdigest(),
        "python": platform.python_version(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "extractor_config": config,
        "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES"),
        "upstream_commit": WISE_COMMIT,
        "object_microbatch": 1,
    }


def validate_evidence(evidence, *, capability, manifest_digest, runtime, nonce=None):
    if (
        capability != "object"
        or not isinstance(evidence, dict)
        or evidence.get("protocol") != PROTOCOL
        or evidence.get("capability") != capability
        or evidence.get("manifest_digest") != manifest_digest
        or evidence.get("runtime") != runtime
        or not isinstance(evidence.get("nonce"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", evidence["nonce"])
        or (nonce is not None and evidence["nonce"] != nonce)
        or not isinstance(evidence.get("checks"), dict)
        or set(evidence["checks"]) != CHECKS
        or any(value is not True for value in evidence["checks"].values())
    ):
        raise ValueError("Probe evidence does not match this installation")
    outputs = evidence.get("outputs", {})
    for name in ("image_vectors", "video_vectors", "text_results", "region_results"):
        value = outputs.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 < value <= 100000
        ):
            raise ValueError("Probe has no validated outputs")
    for name in ("image_sha256", "video_sha256", "results_sha256"):
        value = outputs.get(name)
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("Probe artifact hash is missing")
    config = runtime["extractor_config"]
    from wise_bridge import MODELS

    expected_device = {
        **config.get("default", {}),
        **config.get(MODELS["object"], {}),
    }.get("device", "")
    if (
        outputs.get("device") not in {"cpu", "cuda"}
        or outputs["device"] != expected_device.split(":")[0]
    ):
        raise ValueError("Probe device was not recorded")
    if outputs.get("video_frames") != 8:
        raise ValueError("Probe did not exercise a complete eight-frame chunk")
    return evidence


def _key_path(settings):
    return settings.data_dir / ".optional-probe" / "attestation.key"


def _read_key(settings):
    path = _key_path(settings)
    if path.is_symlink() or (os.name != "nt" and path.stat().st_mode & 0o077):
        raise ValueError("Probe key must be private to the service owner")
    raw = path.read_bytes()
    if len(raw) != 32:
        raise ValueError("Invalid probe key")
    return raw


def _create_key(settings):
    path = _key_path(settings)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(secrets.token_bytes(32))
    return _read_key(settings)


def _receipt_path(settings, capability):
    return settings.data_dir / ".optional-probe" / f"{capability}.receipt.json"


def record_probe(settings, evidence, *, manifest_digest, runtime, nonce):
    """Called by explicit CLI after a successful child, never exposed over HTTP."""
    validate_evidence(
        evidence,
        capability="object",
        manifest_digest=manifest_digest,
        runtime=runtime,
        nonce=nonce,
    )
    payload = {
        "evidence": evidence,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    record = {
        "payload": payload,
        "signature": hmac.new(
            _create_key(settings), canonical(payload), hashlib.sha256
        ).hexdigest(),
    }
    target = _receipt_path(settings, "object")
    temporary = target.with_name(target.name + "." + secrets.token_hex(8) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(record))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def verified_receipt(settings, capability, manifest_digest):
    """Small public proof descriptor; all filesystem/probe details stay private."""
    from wise_bridge.optional_models import _read_json

    try:
        record, digest = _read_json(_receipt_path(settings, capability))
        expected = hmac.new(
            _read_key(settings), canonical(record["payload"]), hashlib.sha256
        ).hexdigest()
        if not isinstance(record.get("signature"), str) or not hmac.compare_digest(
            expected, record["signature"]
        ):
            return None
        evidence = record["payload"]["evidence"]
        validate_evidence(
            evidence,
            capability=capability,
            manifest_digest=manifest_digest,
            runtime=runtime_identity(),
        )
        return {
            "protocol": PROTOCOL,
            "receipt_digest": digest,
            "implementation_digest": evidence["runtime"]["implementation"],
        }
    except (OSError, ValueError, TypeError, KeyError):
        return None
