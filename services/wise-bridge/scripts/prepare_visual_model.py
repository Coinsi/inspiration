"""Populate only the pinned visual model in the isolated WISE HF cache.

Run with --download only when model download is authorized. Follow with an
offline extraction/query test. No audio, face, object, or duplicate .bin weights
are downloaded. This prepares files, not a claim of successful inference.
"""

import argparse
import json
from pathlib import Path

REPOSITORY = "timm/ViT-B-16-SigLIP2-512"
REVISION = "5e7f4f980e465036486ce78dfc721bbedf291939"
FILES = (
    "open_clip_model.safetensors",
    "open_clip_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    print(json.dumps({"repository": REPOSITORY, "revision": REVISION, "files": FILES}))
    if not args.download:
        return
    from huggingface_hub import hf_hub_download

    model_cache = None
    for filename in FILES:
        path = Path(
            hf_hub_download(repo_id=REPOSITORY, revision=REVISION, filename=filename)
        )
        if path.parent.name != REVISION or path.name != filename or not path.is_file():
            raise RuntimeError("Unexpected Hugging Face cache layout")
        model_cache = path.parents[2]
        print(f"Cached {filename}", flush=True)
    # WISE/open_clip requests the named model's main ref. Resolve that ref to
    # exactly these verified snapshot files while HF_HUB_OFFLINE=1 is active.
    refs = model_cache / "refs"
    refs.mkdir(exist_ok=True)
    temporary = refs / "main.wise-preparing"
    temporary.write_text(REVISION, encoding="ascii")
    temporary.replace(refs / "main")
    print(
        "Visual cache prepared. Run WISE with HF_HUB_OFFLINE=1 to preserve this revision."
    )


if __name__ == "__main__":
    main()
