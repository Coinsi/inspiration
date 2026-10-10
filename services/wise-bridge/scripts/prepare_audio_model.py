"""Prepare exactly the pinned WISE Microsoft CLAP 2023 and its GPT-2 dependency.

Default: print the download manifest without network access or loading models.
--download: explicit opt-in download of about 1.24 GB; verify every SHA-256 before
publishing local main refs. --verify-cache: verify only existing cached files and
refs, without downloads or modifications. Use the isolated WISE environment.
"""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path


def verify_file(path, spec):
    if not path.is_file() or path.stat().st_size != spec["size"]:
        raise RuntimeError(f"Model file size mismatch: {spec['filename']}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != spec["sha256"]:
        raise RuntimeError(f"Model file SHA-256 mismatch: {spec['filename']}")


def prepare(manifest, downloader, *, verify_only=False):
    refs_to_publish = []
    for model in manifest["models"]:
        model_cache = None
        for spec in model["files"]:
            # Use the literal 'gpt2' alias: upstream calls from_pretrained('gpt2')
            # and therefore looks in models--gpt2, not models--openai-community--gpt2.
            path = Path(
                downloader(
                    repo_id=model["repository"],
                    revision=model["revision"],
                    filename=spec["filename"],
                    local_files_only=verify_only,
                )
            )
            expected_folder = "models--" + model["repository"].replace("/", "--")
            if (
                path.parent.name != model["revision"]
                or path.name != spec["filename"]
                or path.parents[2].name != expected_folder
            ):
                raise RuntimeError("Unexpected Hugging Face cache layout")
            verify_file(path, spec)
            model_cache = path.parents[2]
            print(f"Verified {model['repository']}/{spec['filename']}", flush=True)
        refs_to_publish.append((model_cache / "refs" / "main", model["revision"]))
    # Never mark either dependency ready before every locked file is verified.
    for path, revision in refs_to_publish:
        if verify_only:
            if (
                not path.is_file()
                or path.read_text(encoding="ascii").strip() != revision
            ):
                raise RuntimeError(
                    "Audio model cache ref does not match the locked revision"
                )
        else:
            path.parent.mkdir(exist_ok=True)
            temporary = path.with_name("main.wise-audio-preparing")
            temporary.write_text(revision, encoding="ascii")
            temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--download", action="store_true")
    modes.add_argument("--verify-cache", action="store_true")
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "audio-model.lock.json",
    )
    args = parser.parse_args()
    manifest = json.loads(args.lock.read_text(encoding="utf-8"))
    total = sum(spec["size"] for model in manifest["models"] for spec in model["files"])
    print(json.dumps({"total_bytes": total, **manifest}, indent=2))
    if not args.download and not args.verify_cache:
        return
    package = manifest["package"]
    if importlib.metadata.version(package["name"]) != package["version"]:
        raise RuntimeError("Installed msclap differs from the audited package version")
    from huggingface_hub import hf_hub_download

    prepare(manifest, hf_hub_download, verify_only=args.verify_cache)
    print(
        "Cache verified. Keep HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1 for inference."
    )
    print("Model readiness still requires the opt-in real WISE audio test.")


if __name__ == "__main__":
    main()
