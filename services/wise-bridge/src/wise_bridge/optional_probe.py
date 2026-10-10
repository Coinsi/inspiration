"""Explicit operator-only OWLv2 acceptance, using existing local weights only."""

import argparse
import json
import secrets
import shutil
import uuid
from dataclasses import replace
from pathlib import Path

from wise_bridge.config import Settings
from wise_bridge.optional_attestation import record_probe, runtime_identity
from wise_bridge.optional_models import OptionalModelUnavailable, inspect_optional
from wise_bridge.runner import ProcessRunner


def run_probe(image, settings=None):
    settings = settings or Settings()
    # This modifies only this explicit CLI's immutable settings. No persisted
    # capability switch or running service configuration is edited.
    settings = replace(settings, enabled_capabilities=("object",))
    before = inspect_optional("object", settings)
    if not before.get("integrity_verified"):
        raise OptionalModelUnavailable("object", before["reason"])
    image = Path(image)
    if not image.is_file() or not 0 < image.stat().st_size <= 4 * 1024**2:
        raise ValueError("Probe image must be a local image no larger than 4 MiB")
    runtime = runtime_identity()
    nonce = secrets.token_hex(32)
    directory = settings.data_dir / ".optional-probe" / "runs" / str(uuid.uuid4())
    directory.mkdir(mode=0o700, parents=True)
    request = {
        "nonce": nonce,
        "manifest_digest": before["manifest_digest"],
        "feature_fingerprint": before["feature_fingerprint"],
        "runtime": runtime,
        "image_id": str(uuid.uuid4()),
        "video_id": str(uuid.uuid4()),
    }
    request_path = directory / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    output = directory / "evidence.json"
    runner = ProcessRunner(settings)
    try:
        # No operator-provided precomputed evidence can be signed: each run gets
        # a fresh private directory/nonce and launches every real inference stage.
        (directory / "input.image").write_bytes(image.read_bytes())
        for stage in ("prepare", "extract", "index", "query"):
            runner.run(
                ["-m", "wise_bridge.optional_probe_worker", stage, str(directory)],
                directory,
                lambda: False,
                timeout=min(settings.process_timeout_seconds, 1800),
            )
        if output.stat().st_size > 128 * 1024:
            raise ValueError("Probe output exceeds limit")
        evidence = json.loads(output.read_text(encoding="utf-8"))
        after = inspect_optional("object", settings)
        if (
            not after.get("integrity_verified")
            or after.get("manifest_digest") != before["manifest_digest"]
            or runtime_identity() != runtime
        ):
            raise ValueError("Model installation changed during the probe")
        record_probe(
            settings,
            evidence,
            manifest_digest=before["manifest_digest"],
            runtime=runtime,
            nonce=nonce,
        )
        return inspect_optional("object", settings)
    finally:
        runner.close_queries()
        # These exact paths were allocated by this invocation; never follow a
        # source path or clean a shared WISE project/model cache.
        for name in ("source", "engine"):
            child = directory / name
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
        (directory / "input.image").unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capability", required=True, choices=["object"])
    parser.add_argument(
        "--image",
        required=True,
        type=Path,
        help="Owned local image containing clear objects; at most 4 MiB",
    )
    args = parser.parse_args()
    state = run_probe(args.image)
    print(
        json.dumps(
            {key: state[key] for key in ("ready", "availability", "validation")},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
