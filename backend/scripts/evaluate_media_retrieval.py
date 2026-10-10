"""Opt-in, local WISE ranking diagnostics on a small, explicitly labeled corpus.

Default: prepare source/hash/label evidence only, without a database or ML work.
Run --phase all --execute only after setting INSPIRATION_RETRIEVAL_EVAL=1 and
BENCHMARK_WISE_URL/TOKEN (loopback Docker service). BENCHMARK_DATABASE_URL is
optional; the application DB hosts an isolated schema, never its normal tables.
--phase query persists traces before --phase score computes offline metrics.
--phase all always attempts owned-resource cleanup, even after a failed query.
Failed cleanup retains remote identities and can be resumed with --phase cleanup.

This measures ranking, not generation, ASR, production capacity or user-approved
relevance. Empty-relevance queries have no Recall/MRR and no inferred threshold.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import benchmark_media_pipeline as bench  # noqa: E402

ROOT = bench.REPO / ".runtime" / "media-retrieval-eval"
CORPUS = bench.REPO / ".runtime" / "media-integration" / "real-retrieval"
LABEL_STATUS = "Experimental labels visually reviewed by root/assistant; not user-approved"
WISE_COMMIT = "fcfa443fbb46eb361bb19151339338616838a5b5"


def default_queries():
    pairs = [
        (
            "white-cup",
            "冒着蒸汽的白色杯子",
            "A white cup with steam rising from it",
            ["cup-image", "cup-video"],
        ),
        (
            "beige",
            "纯米色背景前的木桌上，一只冒蒸汽的白色无柄杯",
            "A steaming white handleless cup on a wooden table against a plain beige background",
            ["cup-image"],
        ),
        (
            "window",
            "窗边木桌上冒着蒸汽的白色杯子",
            "A steaming white cup on a wooden table beside a window",
            ["cup-video"],
        ),
        (
            "portal",
            "紫色发光的太极图案和巨大的法阵门",
            "A huge glowing purple magical portal with a yin-yang symbol",
            ["fantasy-image"],
        ),
        (
            "person",
            "身穿黑衣的长发人物背影",
            "The back of a long-haired person in black robes",
            ["fantasy-image"],
        ),
        (
            "composition",
            "黑衣长发人物背对镜头，站在紫色发光的太极法阵门前",
            "A long-haired figure in black robes seen from behind, "
            "standing before a glowing purple yin-yang portal",
            ["fantasy-image"],
        ),
        ("absent-fox", "雪地里的一只红狐狸", "A red fox standing in snow", []),
    ]
    return [
        {
            "id": f"{pair}-{language}",
            "pair": pair,
            "language": language,
            "query": query,
            "relevant": relevant,
        }
        for pair, zh, en, relevant in pairs
        for language, query in (("zh", zh), ("en", en))
    ]


def validate_queries(queries, names):
    bench.require(
        isinstance(queries, list) and 1 <= len(queries) <= 100, "Need 1..100 labeled queries"
    )
    seen = set()
    for query in queries:
        identity = query.get("id", "")
        bench.require(
            isinstance(identity, str)
            and bench.re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identity)
            and identity not in seen,
            "Query IDs must be unique safe filenames",
        )
        seen.add(identity)
        bench.require(
            isinstance(query.get("query"), str) and 0 < len(query["query"]) <= 2000,
            "Invalid query text",
        )
        relevant = query.get("relevant")
        bench.require(
            isinstance(relevant, list)
            and all(isinstance(x, str) for x in relevant)
            and len(set(relevant)) == len(relevant)
            and set(relevant) <= names,
            "Relevance labels must identify known distinct corpus samples",
        )
        bench.require(query.get("language") in {"zh", "en"}, "Query language must be zh or en")


def validate_live_opt_in(args):
    bench.require(
        args.execute and os.getenv("INSPIRATION_RETRIEVAL_EVAL") == "1",
        "Live retrieval needs --execute and INSPIRATION_RETRIEVAL_EVAL=1",
    )
    url, token = os.getenv("BENCHMARK_WISE_URL", ""), os.getenv("BENCHMARK_WISE_TOKEN", "")
    target = urlsplit(url)
    bench.require(
        target.scheme in {"http", "https"}
        and target.hostname in {"127.0.0.1", "localhost", "::1"}
        and not target.username
        and not target.password
        and not target.query
        and not target.fragment
        and bool(token),
        "Explicit loopback WISE URL and token are required",
    )


def prepare(args):
    from app.modules.library.media_ingest import probe_media, read_image

    source_dir = args.corpus_dir.resolve(strict=True)
    records = json.loads((source_dir / "provenance.json").read_text(encoding="utf-8"))
    bench.require(
        isinstance(records, list) and 1 <= len(records) <= 25, "Corpus must contain 1..25 samples"
    )
    samples, names, hashes = [], set(), set()
    for record in records:
        name = record["name"]
        bench.require(isinstance(name, str) and name not in names, "Duplicate corpus name")
        names.add(name)
        source = Path(record["file"]).resolve(strict=True)
        bench.require(
            source.parent == source_dir and source.is_file(),
            "Corpus file must stay in its supplied directory",
        )
        size, digest = source.stat().st_size, bench.sha256(source)
        bench.require(
            0 < size <= 64 * 1024**2 and size == record["bytes"],
            "Corpus file size mismatch or >64MiB",
        )
        bench.require(
            digest == record["source_hash"] and digest not in hashes,
            "Corpus hash mismatch or duplicate content",
        )
        hashes.add(digest)
        if record["mime"] in {"image/png", "image/jpeg", "image/webp"}:
            image, _ = read_image(source)
            try:
                info = {
                    "media_type": "image",
                    "duration_ms": None,
                    "width": image.width,
                    "height": image.height,
                }
            finally:
                image.close()
        else:
            bench.require(
                record["mime"] == "video/mp4",
                "This visual diagnostic supports static images or MP4",
            )
            info = probe_media(source, "video", max_duration_ms=30_000)
        samples.append(
            {
                "name": name,
                "path": str(source),
                "sha256": digest,
                "size_bytes": size,
                "generation_id": record.get("generation_id"),
                **info,
            }
        )
    queries = (
        json.loads(args.queries.read_text(encoding="utf-8")) if args.queries else default_queries()
    )
    validate_queries(queries, names)
    run_id = uuid.uuid4().hex
    folder = bench.run_folder(args.root, run_id)
    folder.mkdir(parents=True)
    state = {
        "format": bench.FORMAT,
        "kind": "visual-retrieval-evaluation",
        "run_id": run_id,
        "created_at": bench.now(),
        "user_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "plan": {
            "providers": ["wise"],
            "count": len(samples),
            "exercise_recovery": False,
            "reserve_bytes": 12 * bench.GIB,
            "source_paths": [s["path"] for s in samples],
        },
        "samples": samples,
        "queries": queries,
        "label_status": LABEL_STATUS,
        "completed": ["prepare"],
        "attempts": [],
        "limitations": [
            "Small labeled diagnostic; no production quality claim",
            "Recall@3 is a trivial corpus ceiling when all three versions are returned",
            "Empty-relevance queries are unscored; nearest neighbors do not imply "
            "no-match detection",
            "No paid generation, no external cloud, no user source modification",
        ],
    }
    evidence = bench.Evidence(folder, state)
    evidence.save()
    bench.atomic_json(
        folder / "dataset.json",
        {k: state[k] for k in ("samples", "queries", "label_status", "limitations")},
    )
    return evidence


def load_evidence(args):
    if not args.resume:
        return prepare(args)
    folder = bench.run_folder(args.root, args.resume)
    state = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
    bench.require(
        state.get("kind") == "visual-retrieval-evaluation" and state.get("run_id") == args.resume,
        "Not an owned retrieval evaluation",
    )
    if args.phase not in {"score", "prepare"}:
        bench.require(not state.get("cleaned"), "This evaluation has already been cleaned")
    return bench.Evidence(folder, state)


def rank_versions(hits, versions):
    ranked, seen = [], set()
    for hit in hits:
        version = hit.get("version_id")
        bench.require(version in versions, "Search returned a foreign or unknown version")
        score = hit.get("score")
        bench.require(
            type(score) in (int, float) and math.isfinite(score), "Invalid retrieval score"
        )
        if version not in seen:
            ranked.append(
                {
                    "name": versions[version],
                    "version_id": version,
                    "score": score,
                    "start_ms": hit.get("start_ms"),
                    "end_ms": hit.get("end_ms"),
                }
            )
            seen.add(version)
    return ranked


def score_trace(query, trace):
    ranked = [hit["name"] for hit in trace["ranked_versions"]]
    bench.require(len(ranked) == len(set(ranked)), "Metrics require version-deduplicated rankings")
    relevant = set(query["relevant"])
    common = {
        "id": query["id"],
        "pair": query.get("pair"),
        "language": query["language"],
        "query": query["query"],
        "relevant": sorted(relevant),
        "ranking": ranked,
        "raw_hit_count": len(trace["raw_hits"]),
        "unique_hit_count": len(ranked),
    }
    if not relevant:
        return {
            **common,
            "scored": False,
            "reason": "empty relevance set; no calibrated no-match threshold",
            "top_score": trace["ranked_versions"][0]["score"] if ranked else None,
        }
    first = next((i for i, name in enumerate(ranked, 1) if name in relevant), None)
    return {
        **common,
        "scored": True,
        "reciprocal_rank": 1 / first if first else 0.0,
        "recall": {str(k): len(relevant & set(ranked[:k])) / len(relevant) for k in (1, 2, 3)},
    }


def score_saved(evidence):
    rows = []
    for query in evidence.state["queries"]:
        path = evidence.folder / "traces" / (query["id"] + ".json")
        bench.require(path.is_file(), "Save all query traces before scoring")
        trace = json.loads(path.read_text(encoding="utf-8"))
        bench.require(trace["query"] == query["query"], "Trace query differs from immutable labels")
        rows.append(score_trace(query, trace))
    aggregate = {}
    for language in ("all", "zh", "en"):
        selected = [
            r for r in rows if r["scored"] and (language == "all" or r["language"] == language)
        ]
        aggregate[language] = {
            "query_count": len(selected),
            "mrr": sum(r["reciprocal_rank"] for r in selected) / len(selected)
            if selected
            else None,
            "recall": {
                str(k): sum(r["recall"][str(k)] for r in selected) / len(selected)
                if selected
                else None
                for k in (1, 2, 3)
            },
        }
    result = {
        "label_status": LABEL_STATUS,
        "aggregation": "macro average per labeled query; unscored negatives excluded",
        "metrics": aggregate,
        "queries": rows,
        "limitations": evidence.state["limitations"],
        "quality_pass_threshold": None,
    }
    bench.atomic_json(evidence.folder / "report.json", result)
    return result


class EvaluationPipeline(bench.Pipeline):
    def runtime_health(self, filename):
        from app.modules.library.providers.wise import WiseClient

        health = WiseClient(
            os.environ["BENCHMARK_WISE_URL"], os.environ["BENCHMARK_WISE_TOKEN"]
        ).health()
        bench.require(
            health.get("service") == "inspiration-wise-bridge"
            and health.get("upstream_commit") == WISE_COMMIT
            and health.get("worker_alive")
            and health.get("capabilities", {}).get("visual", {}).get("configured"),
            "WISE visual runtime is not ready for real evaluation",
        )
        # Cold workers validate the installed upstream source on their first real
        # job. Do not turn the informational pre-job flag into a dead startup gate.
        bench.atomic_json(
            self.folder / filename,
            {
                k: health[k]
                for k in (
                    "service",
                    "upstream_commit",
                    "capabilities",
                    "upstream_installation_verified",
                    "worker_alive",
                    "models",
                )
                if k in health
            },
        )

    def index(self, entry):
        self.runtime_health("runtime-health.json")
        for sample in self.state["samples"]:
            runs = sample.setdefault("providers", {})
            if "wise" not in runs:
                listing = self.request(
                    "GET", f"/processing/runs?version_id={sample['version_id']}&limit=100"
                )
                existing = [
                    r
                    for r in listing["items"]
                    if r["provider"] == "wise" and r["operation"] == "process"
                ]
                bench.require(len(existing) <= 1, "Ambiguous evaluation processing identity")
                run = (
                    existing[0]
                    if existing
                    else self.request(
                        "POST",
                        f"/versions/{sample['version_id']}/process",
                        json={"provider": "wise", "capabilities": ["visual"]},
                    )
                )
                runs["wise"] = {"id": run["id"]}
                self.evidence.save()
            self.poll(runs["wise"]["id"])
            runs["wise"]["ready"] = True
            self.evidence.save()
        entry["ready_versions"] = len(self.state["samples"])
        self.runtime_health("runtime-health-after-index.json")

    def query(self, entry):
        versions = {s["version_id"]: s["name"] for s in self.state["samples"]}
        folder = self.folder / "traces"
        folder.mkdir(exist_ok=True)
        for query in self.state["queries"]:
            path = folder / (query["id"] + ".json")
            if path.exists():
                saved = json.loads(path.read_text(encoding="utf-8"))
                bench.require(saved["query"] == query["query"], "Saved query differs from labels")
                # A driver may stop between the raw-trace and ranked-trace writes.
                # Reconcile locally without submitting the query a second time.
                if "ranked_versions" not in saved:
                    saved["ranked_versions"] = rank_versions(saved["raw_hits"], versions)
                    bench.atomic_json(path, saved)
                continue
            started = time.monotonic()
            result = self.request(
                "POST",
                "/search",
                json={
                    "engine": "wise",
                    "target": "visual",
                    "query": query["query"],
                    "limit": 100,
                    "collapse_versions": False,
                },
            )
            hits = [
                {k: hit.get(k) for k in ("version_id", "score", "start_ms", "end_ms", "match_type")}
                for hit in result["items"]
            ]
            # Persist raw time-slice order first. Metrics are computed in a later step.
            trace = {
                "query": query["query"],
                "query_id": query["id"],
                "recorded_at": bench.now(),
                "seconds": time.monotonic() - started,
                "coverage": result.get("coverage"),
                "raw_hits": hits,
                "ranking_rule": "first appearance of each version in API order",
            }
            bench.atomic_json(path, trace)
            trace["ranked_versions"] = rank_versions(hits, versions)
            bench.atomic_json(path, trace)
        entry["query_count"] = len(self.state["queries"])

    def verify_sources(self):
        from app.models.storage import Blob
        from app.storage import cas

        for sample in self.state["samples"]:
            version = self.request("GET", f"/versions/{sample['version_id']}")
            bench.require(
                bench.sha256(sample["path"]) == version["original_hash"] == sample["sha256"],
                "Original source changed",
            )
            with self.sessions() as db:
                with cas.local_file(db.get(Blob, version["original_hash"])) as source:
                    bench.require(bench.sha256(source) == sample["sha256"], "CAS original changed")
        self.state["originals_verified"] = True
        self.evidence.save()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=CORPUS)
    parser.add_argument("--queries", type=Path, help="Optional explicit query/relevance JSON list")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--resume")
    parser.add_argument(
        "--phase",
        choices=("prepare", "index", "query", "score", "cleanup", "all"),
        default="prepare",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    args.retry_failed = False
    bench.require(30 <= args.timeout <= 3600, "Timeout must be 30..3600 seconds")
    bench.require(
        args.resume or args.phase not in {"query", "score", "cleanup"},
        "Query, score and cleanup require --resume RUN_ID",
    )
    return args


def main(argv=None):
    args = parse_args(argv)
    evidence = None
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with bench.exclusive_run(bench.REPO / ".runtime" / "media-benchmark"):
            if args.phase in {"index", "query", "cleanup", "all"}:
                validate_live_opt_in(args)
            evidence = load_evidence(args)
            print(json.dumps({"run_id": evidence.state["run_id"], "evidence": str(evidence.path)}))
            if args.phase == "prepare":
                return 0
            if args.phase == "score":
                score_saved(evidence)
                return 0
            if args.phase != "cleanup":
                bench.require(
                    shutil.disk_usage(evidence.folder).free >= 12 * bench.GIB,
                    "Keep at least 12GiB host free before this small evaluation",
                )
            pipeline = EvaluationPipeline(evidence, args)
            with pipeline.connect():
                try:
                    selected = (
                        ["upload", "preview", "index"] if args.phase in {"index", "all"} else []
                    )
                    if args.phase in {"query", "all"}:
                        selected.append("query")
                    for phase in selected:
                        if phase in evidence.state["completed"]:
                            continue
                        if phase == "query":
                            bench.require(
                                "index" in evidence.state["completed"],
                                "Index all samples before querying",
                            )
                        with evidence.phase(phase) as entry:
                            getattr(pipeline, phase)(entry)
                    if args.phase in {"query", "all"}:
                        pipeline.verify_sources()
                    if args.phase == "all":
                        score_saved(evidence)
                finally:
                    if args.phase in {"cleanup", "all"}:
                        with evidence.phase("cleanup") as entry:
                            pipeline.cleanup(entry)
            return 0
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "execution_error": str(exc)
                    if isinstance(exc, bench.BenchmarkError)
                    else type(exc).__name__,
                    "evidence": str(evidence.path) if evidence else None,
                }
            )
        )
        return 1
    finally:
        logging.disable(previous_logging)


if __name__ == "__main__":
    raise SystemExit(main())
