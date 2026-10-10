"""Prepare exactly the locked OWLv2 artifacts, without enabling inference.

Default: print the public manifest only. --download explicitly permits network
in this tool process; --verify-cache is read-only/offline. Other model namespaces
and the running bridge's environment are never modified.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
from pathlib import Path

LOCK = Path(__file__).with_name("object-model.lock.json")
MODEL_ID = "transformers/owlv2/google/owlv2-base-patch16-ensemble"
UPSTREAM_COMMIT = "fcfa443fbb46eb361bb19151339338616838a5b5"
DEPENDENCIES = ("transformers", "torch", "torchvision", "numpy", "sqlalchemy")


def verify_file(path, entry, boundary):
    real = path.resolve()
    if not real.is_relative_to(boundary.resolve()) or not real.is_file():
        raise ValueError("Model file is absent or outside its cache namespace")
    if real.stat().st_size != entry["bytes"]:
        raise ValueError("Model file length differs from the official lock")
    digest = hashlib.sha256()
    with real.open("rb") as stream:
        while chunk := stream.read(1024**2):
            digest.update(chunk)
    if digest.hexdigest() != entry["sha256"]:
        raise ValueError("Model file SHA-256 differs from the official lock")


def prepare(lock, *, download=False):
    hub = Path(
        os.getenv(
            "HF_HUB_CACHE",
            os.getenv(
                "HUGGINGFACE_HUB_CACHE",
                str(
                    Path(
                        os.getenv(
                            "HF_HOME", str(Path.home() / ".cache" / "huggingface")
                        )
                    )
                    / "hub"
                ),
            ),
        )
    )
    repo = hub / "models--google--owlv2-base-patch16-ensemble"
    snapshot = repo / "snapshots" / lock["revision"]
    if download:
        os.environ["HF_HUB_OFFLINE"] = "0"
        # Require room for the complete intended artifacts plus the normal WISE
        # reserve; this conservative estimate also covers resumable cache files.
        boundary = hub
        while not boundary.exists():
            boundary = boundary.parent
        reserve = int(os.getenv("WISE_BRIDGE_MIN_FREE_BYTES", str(2 * 1024**3)))
        if shutil.disk_usage(boundary).free < lock["total_bytes"] + reserve:
            raise RuntimeError("Insufficient free space for the locked model")
        from huggingface_hub import hf_hub_download

        for entry in lock["files"]:
            path = Path(
                hf_hub_download(
                    repo_id=lock["repository"],
                    revision=lock["revision"],
                    filename=entry["name"],
                    cache_dir=str(hub),
                )
            )
            if path != snapshot / entry["name"]:
                raise ValueError("Unexpected Hugging Face cache layout")
            verify_file(path, entry, repo)
            print("Verified " + entry["name"], flush=True)
        refs = repo / "refs"
        refs.mkdir(exist_ok=True)
        temporary = refs / "main.wise-object-preparing"
        temporary.write_text(lock["revision"], encoding="ascii")
        temporary.replace(refs / "main")
    for entry in lock["files"]:
        verify_file(snapshot / entry["name"], entry, repo)
    if (repo / "refs" / "main").read_text().strip() != lock["revision"]:
        raise ValueError("Model main ref differs from the fixed revision")
    return {package: importlib.metadata.version(package) for package in DEPENDENCIES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--download", action="store_true")
    action.add_argument("--verify-cache", action="store_true")
    parser.add_argument(
        "--write-manifest",
        type=Path,
        help="Explicit path for an operator-reviewed object manifest",
    )
    parser.add_argument(
        "--purpose",
        choices=["development_evaluation", "commercial", "noncommercial_research"],
    )
    parser.add_argument(
        "--license-reference", help="Actual operator license review reference"
    )
    args = parser.parse_args()
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if args.write_manifest and not (
        args.purpose and args.license_reference and (args.download or args.verify_cache)
    ):
        parser.error(
            "Writing a manifest requires a verified cache and an explicit purpose/license reference"
        )
    print(json.dumps(lock, ensure_ascii=False))
    if not args.download and not args.verify_cache:
        return
    dependencies = prepare(lock, download=args.download)
    print(
        json.dumps(
            {
                "integrity_verified": True,
                "inference_verified": False,
                "dependencies": dependencies,
            },
            ensure_ascii=False,
        )
    )
    if args.write_manifest:
        manifest = {
            "schema_version": 1,
            "capability": "object",
            "feature_id": MODEL_ID,
            "upstream_commit": UPSTREAM_COMMIT,
            "revision": lock["revision"],
            "files": lock["files"],
            "dependencies": dependencies,
            "license_record": {
                "purpose": args.purpose,
                "license": "Apache-2.0",
                "reference": args.license_reference,
            },
        }
        args.write_manifest.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation never silently replaces an existing license record.
        with args.write_manifest.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2)
            stream.write("\n")


if __name__ == "__main__":
    main()
