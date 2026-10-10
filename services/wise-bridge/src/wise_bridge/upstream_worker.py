"""Runs only in the isolated, pinned WISE Python environment.

No alternative embedding/search algorithm is provided. Public service code never
imports the ML stack; this command uses WISE's own feature and search services.
"""

import importlib.metadata
import json
import math
import os
import subprocess
import sys
import traceback
from pathlib import Path

from wise_bridge import MODELS, WISE_COMMIT
from wise_bridge.object_query import clip_bbox_xywh
from wise_bridge.query_errors import ReferenceQueryError


def verify_installation():
    source = os.environ.get("WISE_SOURCE_DIR")
    if source:
        found = subprocess.check_output(
            ["git", "-C", source, "rev-parse", "HEAD"], text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "-C", source, "status", "--porcelain", "--untracked-files=no"],
            text=True,
        ).strip()
        if found != WISE_COMMIT or dirty:
            raise RuntimeError("WISE source checkout is not the clean pinned revision")
        import wise

        if not Path(wise.__file__).resolve().is_relative_to(Path(source).resolve()):
            raise RuntimeError("Imported WISE does not belong to the pinned checkout")
    else:
        distribution = importlib.metadata.distribution("wise")
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
        if direct.get("vcs_info", {}).get("commit_id") != WISE_COMMIT:
            raise RuntimeError("WISE must be installed from the pinned Git revision")
    return {"commit": WISE_COMMIT, "installed": True, "models_verified": False}


def inspect_project(project_dir, source_ids):
    import sqlalchemy as sa
    from wise import db

    from wise_bridge.upstream_compat import open_project

    project = open_project(project_dir)
    media_map = {
        str(item.id): Path(item.path).stem for item in project.get_media_files()
    }
    if len(media_map) != len(source_ids) or set(media_map.values()) != set(source_ids):
        raise RuntimeError("WISE did not ingest every requested source")
    indices = project.load_search_indices()
    actual_models = {}
    for modality, models in indices.items():
        actual_models[str(getattr(modality, "value", modality))] = list(models)
    counts = {source_id: {name: 0 for name in MODELS} for source_id in source_ids}
    table = db.vectors_table
    with project.db_engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                table.c.media_id,
                table.c.modality,
                table.c.feature_extractor_id,
                sa.func.count(),
            ).group_by(table.c.media_id, table.c.modality, table.c.feature_extractor_id)
        )
        for media_id, modality, model, count in rows:
            source_id = media_map.get(str(media_id))
            for capability, feature_id in MODELS.items():
                if model == feature_id and source_id in counts:
                    # WISE deliberately skips missing/failed index files when
                    # loading. Feature rows alone must never mean "ready".
                    if model not in indices.get(modality, {}):
                        raise RuntimeError(
                            "WISE extracted features have no searchable index"
                        )
                    counts[source_id][capability] += count
        for modality, models in indices.items():
            for model, search_index in models.items():
                if model not in MODELS.values():
                    continue
                expected = conn.execute(
                    sa.select(sa.func.count())
                    .select_from(table)
                    .where(
                        table.c.modality == modality,
                        table.c.feature_extractor_id == model,
                    )
                ).scalar_one()
                if search_index.index.ntotal != expected:
                    raise RuntimeError("WISE feature/index counts disagree")
    if any(not any(values.values()) for values in counts.values()):
        raise RuntimeError("No searchable features extracted for one or more sources")
    return {"media_map": media_map, "counts": counts, "models": actual_models}


def search(project_dir, request, cache=None):
    from wise_bridge.optional_models import enforce_optional_runtime

    enforce_optional_runtime(
        [request["modality"]],
        expected_fingerprint=request.get("feature_fingerprint"),
    )
    if request["modality"] in {"face", "object"} and not request.get(
        "feature_fingerprint"
    ):
        raise ValueError("Optional query requires a verified feature fingerprint")
    import numpy as np
    from wise.api.common import BBoxXYWH, MediaQueryTerm, TextQueryTerm
    from wise.api.services.embedding import EmbeddingConfig
    from wise.api.services.embedding.exceptions import NoFeaturesFoundError
    from wise.api.services.search.local import LocalSearchService
    from wise.data_models import ModalityType

    from wise_bridge.search_cache import SearchCache

    capability = request["modality"]
    model = MODELS[capability]
    cache = cache or SearchCache()
    entry = cache.project({**request, "project_dir": project_dir})
    project, mapping = entry.project, entry.mapping
    embedding = cache.embedding(
        model,
        entry.config.feature_extractor_config,
        feature_fingerprint=request.get("feature_fingerprint"),
    )
    service = LocalSearchService(entry.service, embedding)
    allowed = set(request["source_ids"])
    media_ids = [
        int(media_id) for media_id, source_id in mapping.items() if source_id in allowed
    ]
    if not media_ids:
        return []
    if request.get("query_file"):
        bbox = BBoxXYWH(**request["bbox"]) if request.get("bbox") else None
        query = [
            MediaQueryTerm(
                term_id="query",
                is_negative=False,
                src=Path(request["query_file"]).read_bytes(),
                qtype="audio" if capability == "audio" else "visual",
                bbox=bbox,
            )
        ]
    else:
        if capability == "face":
            raise ValueError("Face search requires an image")
        query = [
            TextQueryTerm(term_id="query", is_negative=False, txt=request["query"])
        ]
    modalities = (
        [ModalityType.AUDIO]
        if capability == "audio"
        else [ModalityType.IMAGE, ModalityType.VIDEO]
    )
    rows = []
    features = None
    for modality in modalities:
        if model not in service.search_indices.get(modality, {}):
            continue
        # Authorisation/tombstone scope is enforced BEFORE FAISS top-k, not after.
        vector_ids = project.get_vector_ids(media_ids, modality, model)
        if not vector_ids:
            continue
        if features is None:
            try:
                features = embedding.embed(
                    model, EmbeddingConfig(query_prefix=""), query
                )
            except NoFeaturesFoundError:
                if capability != "object" or not request.get("query_file"):
                    raise
                raise ReferenceQueryError("reference_no_object") from None
        output = service.search_with_feature(
            features,
            modality,
            model,
            0,
            request["limit"],
            {},
            np.asarray(vector_ids, dtype=np.int64),
        )
        for index, metadata in enumerate(output.metadata):
            source_id = mapping.get(str(metadata.media_id))
            score = float(output.distances[index])
            if source_id not in allowed or not math.isfinite(score):
                continue
            if modality == ModalityType.IMAGE:
                # Pinned WISE uses its shared image/video extraction loop to
                # store timestamp=0 even for still-image vectors. The actual
                # index modality is authoritative: a PNG has no timeline, for
                # both whole-frame and object/face region search.
                start = end = None
            else:
                start = (
                    float(metadata.timestamp)
                    if metadata.timestamp is not None
                    else None
                )
                end = (
                    float(metadata.end_timestamp)
                    if metadata.end_timestamp is not None
                    else start
                )
            box = (
                output.ext_metadata[index].bbox
                if index < len(output.ext_metadata)
                else None
            )
            box = clip_bbox_xywh(box)
            if capability in {"face", "object"} and box is None:
                # Do not fabricate a full-frame box for missing, degenerate or
                # entirely padded detections; preserve only visible regions.
                continue
            rows.append(
                {
                    "source_id": source_id,
                    "start_seconds": start,
                    "end_seconds": end,
                    "score": score,
                    "modality": capability,
                    "feature_id": model,
                    "vector_id": str(output.ids[index]),
                    "bbox": box,
                    "match_type": "segment"
                    if end is not None and start != end
                    else "frame"
                    if start is not None
                    else "image",
                }
            )
    # Same model across image/video, so scores are comparable. No fake confidence.
    return sorted(rows, key=lambda row: row["score"], reverse=True)[: request["limit"]]


def serve():
    from wise_bridge.query_process import MAX_MESSAGE_BYTES
    from wise_bridge.search_cache import SearchCache

    # Preserve a dedicated protocol FD and redirect Python *and native-library*
    # stdout to the private stderr log before importing the ML stack.
    with os.fdopen(
        os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1
    ) as protocol:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        sys.stdout = sys.stderr
        verify_installation()
        cache = SearchCache(
            max_models=int(os.environ.get("WISE_QUERY_CACHE_MODELS", "1")),
            max_projects=int(os.environ.get("WISE_QUERY_CACHE_PROJECTS", "2")),
            max_index_bytes=int(
                os.environ.get("WISE_QUERY_CACHE_INDEX_BYTES", str(512 * 1024**2))
            ),
        )
        while True:
            line = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
                raise RuntimeError("Invalid request frame")
            request = json.loads(line)
            request_id = request["request_id"]
            try:
                payload = request["payload"]
                result = search(payload["project_dir"], payload, cache)
                response = {"request_id": request_id, "result": result}
            except ReferenceQueryError as exc:
                response = {"request_id": request_id, "input_error": exc.code}
            except Exception:  # noqa: BLE001 - isolated protocol boundary; logged privately
                traceback.print_exc(file=sys.stderr)
                response = {"request_id": request_id, "error": "Query failed"}
            output = json.dumps(response, allow_nan=False) + "\n"
            if len(output.encode()) > MAX_MESSAGE_BYTES:
                raise RuntimeError("Query response exceeds protocol limit")
            protocol.write(output)
            protocol.flush()


def main():
    if sys.argv[1:] == ["serve"]:
        serve()
        return
    operation, request_path, output_path = sys.argv[1:]
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    verified = verify_installation()
    if operation == "probe":
        result = verified
    elif operation == "inspect":
        result = inspect_project(request["project_dir"], request["source_ids"])
    elif operation == "prune":
        from wise_bridge.feature_reuse import prune_project

        result = prune_project(
            request["project_dir"],
            request["expected_sources"],
            request["retained_sources"],
        )
    elif operation == "merge":
        from wise_bridge.feature_reuse import merge_delta

        result = merge_delta(
            request["project_dir"],
            request["delta_dir"],
            request["retained_sources"],
            request["delta_sources"],
        )
    elif operation == "search":
        result = search(request["project_dir"], request)
    else:
        raise ValueError("Unknown operation")
    Path(output_path).write_text(json.dumps(result, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
