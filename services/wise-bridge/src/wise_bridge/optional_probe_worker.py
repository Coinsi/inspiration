"""Bounded, offline CLI child. No HTTP route imports or executes this module."""

import hashlib
import json
import math
import runpy
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from wise_bridge import MODELS
from wise_bridge.config import Settings
from wise_bridge.optional_attestation import (
    CHECKS,
    PROTOCOL,
    canonical,
    runtime_identity,
)
from wise_bridge.optional_models import explicit_probe_scope


def _wise(arguments):
    sys.argv = ["wise", *arguments]
    try:
        runpy.run_module("wise", run_name="__main__")
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise


def _prepare(directory, request):
    from PIL import Image, ImageOps

    source = directory / "source"
    source.mkdir()
    with Image.open(directory / "input.image") as image:
        if image.width * image.height > 20_000_000:
            raise ValueError("Probe image is too large")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((768, 768))
        # Video encoder requires even dimensions, without changing query framing.
        size = tuple(max(2, value // 2 * 2) for value in image.size)
        image = image.resize(size)
        image.save(source / (request["image_id"] + ".png"))
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-loop",
            "1",
            "-i",
            str(source / (request["image_id"] + ".png")),
            "-t",
            "4",
            "-r",
            "2",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source / (request["video_id"] + ".mp4")),
        ],
        check=True,
        timeout=60,
    )
    (directory / "sources.txt").write_text(
        request["image_id"] + ".png\n" + request["video_id"] + ".mp4\n",
        encoding="utf-8",
    )


def validate_rows(rows, allowed, image_id, video_id):
    if not rows:
        raise ValueError("Probe search returned no usable regions")
    seen = set()
    for row in rows:
        if row["source_id"] not in allowed or row["modality"] != "object":
            raise ValueError("Query escaped the source scope")
        if not isinstance(row["vector_id"], str) or not row["vector_id"].isdecimal():
            raise ValueError("Missing stable vector identity")
        identity = (row["source_id"], row["vector_id"])
        if identity in seen:
            raise ValueError("Repeated vector identity")
        seen.add(identity)
        if not math.isfinite(row["score"]):
            raise ValueError("Non-finite query score")
        bbox = row["bbox"]
        if not isinstance(bbox, dict) or set(bbox) != {"x", "y", "w", "h"}:
            raise ValueError("Missing object region")
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in bbox.values()
        ):
            raise ValueError("Non-finite object region")
        x, y, w, h = (bbox[key] for key in ("x", "y", "w", "h"))
        if not (
            0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x + w <= 1 and y + h <= 1
        ):
            raise ValueError("Object region is outside the frame")
        if row["source_id"] == image_id:
            if row["start_seconds"] is not None or row["end_seconds"] is not None:
                raise ValueError("Still image acquired a false timestamp")
        elif row["source_id"] == video_id and not all(
            isinstance(row[key], (int, float))
            and not isinstance(row[key], bool)
            and math.isfinite(row[key])
            and 0 <= row[key] <= 4
            for key in ("start_seconds", "end_seconds")
        ):
            raise ValueError("Video timestamp is missing or outside the fixture")
    return seen


def _query(directory, request):
    from wise_bridge.search_cache import SearchCache
    from wise_bridge.upstream_worker import inspect_project, search

    image_id, video_id = request["image_id"], request["video_id"]
    engine = str(directory / "engine")
    inspection = inspect_project(engine, [image_id, video_id])
    counts = inspection["counts"]
    if counts[image_id]["object"] <= 0 or counts[video_id]["object"] <= 0:
        raise ValueError("Both image and video must produce real object vectors")
    import sqlalchemy as sa
    from wise import db

    from wise_bridge.upstream_compat import open_project

    inspected = open_project(engine, read_only=True)
    mid = next(
        int(key) for key, value in inspection["media_map"].items() if value == video_id
    )
    with inspected.db_engine.connect() as conn:
        times = (
            conn.execute(
                sa.select(db.vectors_table.c.timestamp)
                .where(
                    db.vectors_table.c.media_id == mid,
                    db.vectors_table.c.feature_extractor_id == MODELS["object"],
                )
                .distinct()
            )
            .scalars()
            .all()
        )
    inspected.db_engine.dispose()
    if sorted(times) != [index / 2 for index in range(8)]:
        raise ValueError("Probe did not process one complete eight-frame video chunk")
    cache = SearchCache()
    base = {
        "project_id": request["nonce"],
        "revision": "probe",
        "modality": "object",
        "feature_fingerprint": request["feature_fingerprint"],
        "limit": 100,
        "source_ids": [image_id, video_id],
        "query": "an object",
    }
    rows = search(engine, base, cache)
    identities = validate_rows(rows, {image_id, video_id}, image_id, video_id)
    # Search each modality through an explicit source whitelist before FAISS topK.
    image_rows = search(engine, {**base, "source_ids": [image_id]}, cache)
    video_rows = search(engine, {**base, "source_ids": [video_id]}, cache)
    validate_rows(image_rows, {image_id}, image_id, video_id)
    validate_rows(video_rows, {video_id}, image_id, video_id)
    if search(engine, {**base, "source_ids": []}, cache) != []:
        raise ValueError("Empty source whitelist returned results")
    region = search(
        engine,
        {
            **base,
            "query": None,
            "query_file": str(directory / "source" / (image_id + ".png")),
            "bbox": image_rows[0]["bbox"],
        },
        cache,
    )
    validate_rows(region, {image_id, video_id}, image_id, video_id)
    repeat = search(engine, base, cache)
    if validate_rows(repeat, {image_id, video_id}, image_id, video_id) != identities:
        raise ValueError("Vector identity changed across identical queries")

    def sha(name):
        return hashlib.sha256((directory / "source" / name).read_bytes()).hexdigest()

    # This is a structural inference acceptance, not a semantic-quality claim.
    return {
        "protocol": PROTOCOL,
        "capability": "object",
        "nonce": request["nonce"],
        "manifest_digest": request["manifest_digest"],
        "runtime": request["runtime"],
        "checks": dict.fromkeys(CHECKS, True),
        "outputs": {
            "image_vectors": counts[image_id]["object"],
            "video_vectors": counts[video_id]["object"],
            "video_frames": len(times),
            "text_results": len(rows),
            "region_results": len(region),
            "image_sha256": sha(image_id + ".png"),
            "video_sha256": sha(video_id + ".mp4"),
            "results_sha256": hashlib.sha256(canonical([rows, region])).hexdigest(),
            "device": str(
                cache.models[MODELS["object"]]
                .feature_extractors[MODELS["object"]]
                .DEVICE
            ).split(":")[0],
        },
    }


def main():
    stage, path = sys.argv[1:]
    directory = Path(path).resolve()
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    settings = replace(Settings(), enabled_capabilities=("object",))
    with explicit_probe_scope(settings, "object") as digest:
        if (
            digest != request["manifest_digest"]
            or runtime_identity() != request["runtime"]
        ):
            raise ValueError("Probe installation changed between stages")
        from wise_bridge.upstream_worker import verify_installation

        verify_installation()
        if stage == "prepare":
            _prepare(directory, request)
        elif stage == "extract":
            _wise(
                [
                    "extract-features",
                    str(directory / "source"),
                    "--media-files-from",
                    str(directory / "sources.txt"),
                    "--project-dir",
                    str(directory / "engine"),
                    "--num-workers",
                    "0",
                    "--shard-maxcount",
                    "4096",
                    "--no-thumbnails",
                    "--yes",
                    "--image-feature-id",
                    MODELS["object"],
                    "--video-feature-id",
                    MODELS["object"],
                ]
            )
        elif stage == "index":
            _wise(
                [
                    "create-index",
                    "--project-dir",
                    str(directory / "engine"),
                    "--index-type",
                    "IndexFlatIP",
                    "--overwrite",
                ]
            )
        elif stage == "query":
            result = _query(directory, request)
            (directory / "evidence.json").write_bytes(canonical(result))
        else:
            raise ValueError("Unknown probe stage")


if __name__ == "__main__":
    main()
