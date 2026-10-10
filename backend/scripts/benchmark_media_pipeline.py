"""Repeatable, isolated media acceptance measurements (never a production load test).

Run from backend with its virtualenv, e.g.::

    python -X utf8 scripts/benchmark_media_pipeline.py
    python -X utf8 scripts/benchmark_media_pipeline.py --resume RUN_ID --phase cleanup
    python -X utf8 scripts/benchmark_media_pipeline.py --count 5 --allow-heavy \
        --previous-run SUCCESSFUL_ONE_FILE_RUN_ID
    python -X utf8 scripts/benchmark_media_pipeline.py --duration 3600 --allow-heavy
    python -X utf8 scripts/benchmark_media_pipeline.py --duration 60 --allow-heavy \
        --providers mediacms,wise --allow-external --previous-duration-run SHORT_RUN_ID
    python -X utf8 scripts/benchmark_media_pipeline.py --source C:/footage/one.mp4 \
        --allow-heavy --providers mediacms,wise --allow-external

BENCHMARK_DATABASE_URL (or application database_url) only hosts a random dedicated
schema. Original project tables and original source files are never changed.
External opt-in also requires BENCHMARK_MEDIACMS_URL/TOKEN and/or
BENCHMARK_WISE_URL/TOKEN in the environment; tokens are never written to evidence.
One selected provider runs at a time. No model weights are installed by this tool.

The default is ONE 6-second, 3840x2160, 12fps synthetic low-bitrate video. Such
footage does NOT establish real high-bitrate 4K or hour-long capacity or retrieval
quality. --source accepts one distinct local file per requested sample. Runs of
5/10/50 require evidence from the preceding 1/5/10 tier. --exercise-recovery tests
controlled staging loss and cooperative encoder interruption, NOT worker kills.
External long-video runs separately require a verified, cleaned, single-source
duration predecessor: short (6..30s) -> 60s -> 300s -> 900s -> 3600s. A short
batch is not a duration predecessor. Local --source durations are probed before
admission; nominal --duration cannot disguise a longer file. This ladder still
only proves the tested input, not high-bitrate or diverse real-footage capacity.

--phase runs one durable phase; --until stops after a phase. Interrupted phases
reconcile server IDs/offsets on --resume. Failed external tasks need --retry-failed.
Timeouts retain the schema and remote identities for reconciliation and cleanup;
cleanup waits for owned remote jobs before dropping only the owned schema/files.
JSON evidence retains failures, timings, sampled RSS/disk, and observed hashes.
Transport is the real FastAPI ASGI app in-process, not browser/network throughput.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
GIB = 1024**3
PHASES = ("sources", "upload", "preview", "mediacms", "wise", "search", "verify")
TIERS = (1, 5, 10, 50)
DURATION_TIERS = (30, 60, 300, 900, 3600)
FORMAT = 1


class BenchmarkError(Exception):
    """Only tool-authored, credential-free messages may be persisted."""


def require(condition, message):
    if not condition:
        raise BenchmarkError(message)


def now():
    return datetime.now(UTC).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def exclusive_run(root):
    """One benchmark process across all run IDs on this filesystem."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".benchmark.lock").open("a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BenchmarkError(
                    "Another benchmark is running; concurrency limit is one"
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise BenchmarkError(
                    "Another benchmark is running; concurrency limit is one"
                ) from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def run_folder(root, run_id):
    # IDs, schema names and destructive paths never come directly from arbitrary input.
    require(re.fullmatch(r"[a-f0-9]{32}", run_id) is not None, "Invalid run ID")
    root = Path(root).resolve()
    target = root / run_id
    require(not target.is_symlink() and target.resolve().parent == root, "Unsafe run directory")
    return target


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=REPO / ".runtime" / "media-benchmark")
    parser.add_argument("--resume", help="Existing 32-hex run ID; loads its immutable plan")
    parser.add_argument("--phase", choices=(*PHASES, "cleanup"))
    parser.add_argument("--until", choices=PHASES)
    parser.add_argument("--count", type=int, choices=TIERS, default=1)
    parser.add_argument("--duration", type=float, default=6)
    parser.add_argument("--bitrate-mbps", type=float, default=2)
    parser.add_argument("--source", type=Path, action="append", default=[])
    parser.add_argument("--providers", default="", help="Optional mediacms,wise (serial)")
    parser.add_argument("--allow-heavy", action="store_true")
    parser.add_argument("--allow-external", action="store_true")
    parser.add_argument("--previous-run", help="Verified prior 1/5/10-file tier")
    parser.add_argument(
        "--previous-duration-run", help="Verified, cleaned single-source duration predecessor"
    )
    parser.add_argument("--concurrency", type=int, default=1, help="Hard maximum is one")
    parser.add_argument("--reserve-gib", type=float, default=10)
    parser.add_argument(
        "--timeout", type=int, default=900, help="Per-sample provider timeout seconds"
    )
    parser.add_argument("--query", default="colourful geometric shapes")
    parser.add_argument("--exercise-recovery", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    return parser.parse_args(argv)


def validate_plan(args):
    require(args.concurrency == 1, "Only concurrency=1 is supported; no parallel heavy jobs")
    require(math.isfinite(args.duration) and 0 < args.duration <= 3600, "Duration must be 0..3600s")
    require(
        math.isfinite(args.bitrate_mbps) and 0.1 <= args.bitrate_mbps <= 100, "Bitrate 0.1..100"
    )
    require(math.isfinite(args.reserve_gib) and args.reserve_gib >= 5, "Reserve must be >=5 GiB")
    require(10 <= args.timeout <= 86400, "Timeout must be 10..86400s")
    require(not (args.phase and args.until), "Choose phase OR until")
    providers = [s for s in args.providers.split(",") if s]
    require(set(providers) <= {"wise", "mediacms"}, "Unknown provider")
    require(not providers or args.allow_external, "External processing needs --allow-external")
    require(
        args.allow_heavy or (args.count == 1 and args.duration <= 30 and args.bitrate_mbps <= 4),
        "Batch, long duration or high bitrate requires --allow-heavy",
    )
    require(not args.source or len(args.source) == args.count, "Provide one --source per sample")
    require(len(args.query) <= 2000, "Query too long")
    return {
        "count": args.count,
        "duration": args.duration,
        "bitrate_mbps": args.bitrate_mbps,
        "source_paths": [str(path.resolve(strict=True)) for path in args.source],
        "providers": sorted(set(providers)),
        "reserve_bytes": int(args.reserve_gib * GIB),
        "concurrency": 1,
        "query": args.query,
        "exercise_recovery": args.exercise_recovery,
    }


def estimate_budget(plan, sources=None):
    count = plan["count"]
    durations = (
        [item["duration_ms"] / 1000 for item in sources] if sources else [plan["duration"]] * count
    )
    total_seconds = sum(durations)
    original = (
        sum(item["size_bytes"] for item in sources)
        if sources
        else math.ceil(total_seconds * (plan["bitrate_mbps"] + 0.3) * 1_000_000 / 8 * 1.25)
    )
    # Conservative headroom, not a promise about codecs or pathological output.
    derived = math.ceil(total_seconds * 12_000_000 / 8)
    local = original * (2 if plan["source_paths"] else 3) + derived * 2 + GIB
    sidecar = 0
    if "mediacms" in plan["providers"]:
        sidecar += original + derived * 4 + GIB
    if "wise" in plan["providers"]:
        sidecar += original + math.ceil(total_seconds * 0.5 * 1024 * 1024) + 2 * GIB
    return {
        "source_bytes": original,
        "local_peak_estimate_bytes": local,
        "sidecar_peak_estimate_bytes": sidecar,
        "combined_peak_estimate_bytes": local + sidecar,
        "reserve_bytes": plan["reserve_bytes"],
        "method": "conservative planning allowance; observed usage is reported separately",
    }


def check_duration_gate(args, plan, sources=None, previous=None):
    """A separate provider-duration ladder; never substitute batch count evidence."""
    if not plan["providers"]:
        return None
    if sources is None and plan["source_paths"]:
        from app.modules.library.media_ingest import probe_media

        # Local metadata/first-frame validation only; no upload or feature extraction.
        sources = [probe_media(path, "video") for path in plan["source_paths"]]
    duration = max(item["duration_ms"] for item in sources) / 1000 if sources else plan["duration"]
    require(
        math.isfinite(duration) and 0 < duration <= 3601.5, "External duration must be <=1 hour"
    )
    if duration <= 30:
        return None
    tier = next(value for value in DURATION_TIERS[1:] if duration <= value + 1.5)
    predecessor = DURATION_TIERS[DURATION_TIERS.index(tier) - 1]
    prior_id = args.previous_duration_run or (previous or {}).get("previous_run")
    if previous and args.previous_duration_run:
        require(
            prior_id == previous["previous_run"], "Duration predecessor cannot change on resume"
        )
    require(
        bool(prior_id),
        f"External duration tier {tier}s needs --previous-duration-run from {predecessor}s tier",
    )
    path = run_folder(args.root, prior_id) / "evidence.json"
    require(path.is_file(), "Duration predecessor evidence does not exist")
    prior = json.loads(path.read_text(encoding="utf-8"))
    require(
        prior.get("format") == FORMAT
        and prior.get("run_id") == prior_id
        and prior.get("verified")
        and prior.get("cleaned")
        and prior.get("schema_dropped"),
        "Duration predecessor must be verified and fully cleaned",
    )
    required = {"sources", "upload", "preview", "search", "verify", *plan["providers"]}
    require(
        required <= set(prior.get("completed", [])), "Duration predecessor phases are incomplete"
    )
    require(
        prior["plan"]["count"] == 1 and len(prior.get("samples", [])) == 1,
        "Duration predecessor must be a single source; a short batch cannot unlock long processing",
    )
    require(
        prior["plan"]["providers"] == plan["providers"],
        "Duration predecessor used different providers",
    )
    require(
        bool(prior["plan"]["source_paths"]) == bool(plan["source_paths"])
        and prior["plan"]["bitrate_mbps"] >= plan["bitrate_mbps"],
        "Duration predecessor used a lighter or different source workload",
    )
    observed = prior["samples"][0]["duration_ms"] / 1000
    require(
        6 <= observed <= 30 if predecessor == 30 else abs(observed - predecessor) <= 1.5,
        f"Duration predecessor must have actually verified {predecessor}s tier; "
        "nominal plan is insufficient",
    )
    return {
        "tier_seconds": tier,
        "observed_or_planned_seconds": duration,
        "previous_run": prior_id,
        "previous_observed_seconds": observed,
        "scope": "single-source duration admission; no real-footage quality or high-bitrate claim",
    }


def check_disk(path, budget):
    available = shutil.disk_usage(path).free
    required = budget["combined_peak_estimate_bytes"] + budget["reserve_bytes"]
    require(available >= required, "Insufficient free disk for estimated peak plus reserve")
    return {"free_bytes": available, "required_bytes": required}


def tree_bytes(folder):
    size = 0
    for parent, _, files in os.walk(folder):
        for name in files:
            try:
                size += (Path(parent) / name).stat().st_size
            except OSError:
                pass
    return size


def process_rss():
    """Current process and its descendants, no process command lines or credentials."""
    records = {}
    if os.name == "nt":
        from ctypes import wintypes as w

        class Entry(ctypes.Structure):
            _fields_ = [
                ("size", w.DWORD),
                ("usage", w.DWORD),
                ("pid", w.DWORD),
                ("heap", ctypes.c_size_t),
                ("module", w.DWORD),
                ("threads", w.DWORD),
                ("parent", w.DWORD),
                ("priority", w.LONG),
                ("flags", w.DWORD),
                ("name", w.WCHAR * 260),
            ]

        class Memory(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("faults", w.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "peak_rss",
                    "rss",
                    "peak_paged",
                    "paged",
                    "peak_nonpaged",
                    "nonpaged",
                    "pagefile",
                    "peak_pagefile",
                )
            ]

        kernel, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
        kernel.OpenProcess.restype = w.HANDLE
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [
            w.HANDLE,
            ctypes.POINTER(Entry),
        ]
        psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.POINTER(Memory), w.DWORD]
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        try:
            item = Entry()
            item.size = ctypes.sizeof(item)
            more = kernel.Process32FirstW(snapshot, ctypes.byref(item))
            while more:
                records[item.pid] = {"parent": item.parent, "rss_bytes": None}
                more = kernel.Process32NextW(snapshot, ctypes.byref(item))
        finally:
            kernel.CloseHandle(snapshot)
        selected = descendants(records, os.getpid())
        for pid in selected:
            handle = kernel.OpenProcess(0x410, False, pid)
            if handle:
                try:
                    memory = Memory()
                    if psapi.GetProcessMemoryInfo(
                        handle, ctypes.byref(memory), ctypes.sizeof(memory)
                    ):
                        records[pid]["rss_bytes"] = memory.rss
                finally:
                    kernel.CloseHandle(handle)
    else:
        for status in Path("/proc").glob("[0-9]*/status"):
            try:
                fields = dict(line.split(":", 1) for line in status.read_text().splitlines())
                records[int(status.parent.name)] = {
                    "parent": int(fields["PPid"]),
                    "rss_bytes": int(fields.get("VmRSS", "0 kB").split()[0]) * 1024,
                }
            except (OSError, ValueError):
                pass
        selected = descendants(records, os.getpid())
    return {
        "self_rss_bytes": records.get(os.getpid(), {}).get("rss_bytes"),
        "process_tree_rss_bytes": sum(records[p]["rss_bytes"] or 0 for p in selected),
        "processes": len(selected),
        "note": "RSS sum includes shared pages; sampled, not an exact peak",
    }


def descendants(records, root):
    selected = {root} if root in records else set()
    while (
        additions := {pid for pid, item in records.items() if item["parent"] in selected} - selected
    ):
        selected.update(additions)
    return selected


def command(args, timeout=10):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def gpu_metrics(container):
    """Whole-device samples, including desktop/other workloads, not container attribution."""
    result = {
        "available": False,
        "scope": "whole visible GPU devices; includes desktop and other processes",
        "measurement": "point sample; observed maxima are not exact peaks or reserved capacity",
    }
    if not re.fullmatch(r"inspiration-wise-services-wise-\d+", container):
        return {**result, "reason": "not_managed_wise_container"}
    try:
        response = command(
            [
                "docker",
                "exec",
                container,
                "nvidia-smi",
                "--query-gpu=index,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            timeout=3,
        )
        if response.returncode != 0:
            return {**result, "reason": "gpu_tool_or_device_unavailable"}

        def value(raw):
            raw = raw.strip()
            if raw in {"N/A", "[N/A]", "[Not Supported]", "Not Supported"}:
                return None
            parsed = float(raw)
            if not math.isfinite(parsed) or parsed < 0:
                raise ValueError("Invalid GPU reading")
            return parsed

        devices = []
        for row in csv.reader(response.stdout.splitlines()):
            if not row:
                continue
            if len(row) != 4 or not row[0].strip().isdigit():
                raise ValueError("Invalid GPU row")
            used, total, utilization = (value(raw) for raw in row[1:])
            if utilization is not None and utilization > 100:
                raise ValueError("Invalid GPU utilization")
            devices.append(
                {
                    "index": int(row[0]),
                    "memory_used_bytes": int(used * 1024**2) if used is not None else None,
                    "memory_total_bytes": int(total * 1024**2) if total is not None else None,
                    "utilization_percent": utilization,
                }
            )
        if not devices:
            return {**result, "reason": "no_visible_device"}
        return {**result, "available": True, "devices": devices}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {**result, "reason": "gpu_reading_unavailable"}


def container_metrics(providers):
    """Read only the known deployment containers; never inspect environment variables."""
    results = []
    projects = {"mediacms": "inspiration-media-services", "wise": "inspiration-wise-services"}
    code = """import json, os, pathlib
rss = 0
for status in pathlib.Path('/proc').glob('[0-9]*/status'):
    try:
        rss += sum(int(line.split()[1])*1024 for line in status.read_text().splitlines()
                   if line.startswith('VmRSS:'))
    except (OSError, ValueError):
        pass
disks = []
for path in ['/data', '/processing-tmp', '/home/mediacms.io/mediacms/media_files']:
    if os.path.isdir(path):
        stat = os.statvfs(path)
        disks.append({'path':path, 'free_bytes':stat.f_bavail*stat.f_frsize,
                      'used_bytes':(stat.f_blocks-stat.f_bfree)*stat.f_frsize,
                      'total_bytes':stat.f_blocks*stat.f_frsize})
cpu = {'available':False, 'scope':'whole container cumulative CPU time'}
try:
    counters = dict(line.split() for line in
                    pathlib.Path('/sys/fs/cgroup/cpu.stat').read_text().splitlines())
    cpu.update(available=True, usage_seconds=int(counters['usage_usec'])/1000000)
except (OSError, ValueError, KeyError):
    pass
print(json.dumps({'rss_sum_bytes':rss, 'disks':disks, 'cpu':cpu}))
"""
    for provider in providers:
        try:
            names = command(
                [
                    "docker",
                    "ps",
                    "--filter",
                    f"label=com.docker.compose.project={projects[provider]}",
                    "--format",
                    "{{.Names}}",
                ]
            ).stdout.splitlines()
            for name in names:
                if not re.fullmatch(
                    re.escape(projects[provider]) + r"-(web|worker|wise)-\d+", name
                ):
                    continue
                response = command(["docker", "exec", name, "python", "-c", code])
                record = json.loads(response.stdout) if response.returncode == 0 else {}
                if provider == "wise":
                    record["gpu"] = gpu_metrics(name)
                results.append({"container": name, "provider": provider, **record})
        except (OSError, ValueError, subprocess.TimeoutExpired):
            results.append({"provider": provider, "unavailable": True})
    return results


class Evidence:
    def __init__(self, folder, state):
        self.folder, self.state = folder, state
        self.path = folder / "evidence.json"

    def save(self):
        self.state["updated_at"] = now()
        atomic_json(self.path, self.state)

    @contextmanager
    def phase(self, name):
        entry = {"phase": name, "status": "running", "started_at": now()}
        self.state["attempts"].append(entry)
        self.save()
        started = time.monotonic()
        stop = threading.Event()
        telemetry = self.folder / "telemetry.jsonl"

        def sample():
            while True:
                try:
                    record = {
                        "time": now(),
                        "phase": name,
                        **process_rss(),
                        "disk_free_bytes": shutil.disk_usage(self.folder).free,
                        "run_disk_bytes": tree_bytes(self.folder),
                        "containers": container_metrics(self.state["plan"]["providers"]),
                    }
                    with telemetry.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(record) + "\n")
                    entry["sampled_peak_tree_rss_bytes"] = max(
                        entry.get("sampled_peak_tree_rss_bytes", 0),
                        record["process_tree_rss_bytes"],
                    )
                    entry["minimum_disk_free_bytes"] = min(
                        entry.get("minimum_disk_free_bytes", record["disk_free_bytes"]),
                        record["disk_free_bytes"],
                    )
                except Exception:
                    entry["telemetry_incomplete"] = True
                if stop.wait(0.25 if not self.state["plan"]["providers"] else 2):
                    break

        thread = threading.Thread(target=sample, daemon=True)
        thread.start()
        try:
            yield entry
            entry["status"] = "passed"
        except BaseException as exc:
            entry["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            # Driver/API exceptions can contain connection strings or provider URLs.
            entry["error_type"] = type(exc).__name__
            if isinstance(exc, BenchmarkError):
                entry["error"] = str(exc)
            raise
        finally:
            elapsed = time.monotonic() - started
            stop.set()
            # Sampler subprocesses have deadlines; finish before final evidence.
            thread.join()
            entry["elapsed_seconds"] = round(elapsed, 3)
            entry["finished_at"] = now()
            if entry["status"] == "passed" and name not in self.state["completed"]:
                self.state["completed"].append(name)
            self.save()


class Pipeline:
    def __init__(self, evidence, args):
        self.evidence, self.args = evidence, args
        self.state = evidence.state
        self.plan = self.state["plan"]
        self.folder = evidence.folder
        self.schema = "bench_media_" + self.state["run_id"]

    @contextmanager
    def connect(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.schema import CreateSchema

        from app.core.config import settings
        from app.core.database import get_db
        from app.core.errors import AppError, app_error_handler
        from app.core.security import create_access_token
        from app.models import Base
        from app.models.identity import Membership, Project, User
        from app.modules.library import processing_worker, worker
        from app.modules.library.router import router

        # A minimal real library router has no app lifespan/background workers.
        # Settings and session injection are isolation, not replacement media logic.
        admin = create_engine(os.environ.get("BENCHMARK_DATABASE_URL") or settings.database_url)
        require(admin.dialect.name == "postgresql", "A PostgreSQL database is required")
        translated = admin.execution_options(schema_translate_map={None: self.schema})
        self.admin, self.sessions = admin, sessionmaker(bind=translated, autoflush=False)
        app = FastAPI()
        app.add_exception_handler(AppError, app_error_handler)
        app.include_router(router, prefix="/api/v1")

        def database():
            with self.sessions() as db:
                try:
                    yield db
                    db.commit()
                except Exception:
                    db.rollback()
                    raise

        app.dependency_overrides[get_db] = database
        try:
            with admin.begin() as conn:
                exists = conn.scalar(
                    text("SELECT 1 FROM pg_namespace WHERE nspname=:n"), {"n": self.schema}
                )
                if not exists:
                    require(
                        not self.state.get("schema_initialized")
                        or self.state.get("schema_dropped"),
                        "Isolated schema disappeared; do not recreate successful evidence",
                    )
                    conn.execute(CreateSchema(self.schema))
                    conn.execute(
                        text(
                            f'CREATE TABLE "{self.schema}"._benchmark_owner '
                            "(run_id text PRIMARY KEY)"
                        )
                    )
                    conn.execute(
                        text(f'INSERT INTO "{self.schema}"._benchmark_owner VALUES (:v)'),
                        {"v": self.state["run_id"]},
                    )
                self.check_owner(conn)
            Base.metadata.create_all(translated)
            self.state["schema_initialized"] = True
            self.evidence.save()
            with ExitStack() as stack:
                overrides = {
                    "storage_backend": "fs",
                    "fs_storage_dir": str(self.folder / "cas"),
                    "library_staging_dir": str(self.folder / "staging"),
                    "library_processing_temp_dir": str(self.folder / "transfer"),
                    "library_processing_enabled": True,
                    "library_processing_auto": False,
                    "secret_key": uuid.uuid4().hex + uuid.uuid4().hex,
                }
                for provider in self.plan["providers"]:
                    for field in ("url", "token"):
                        value = os.environ.get(f"BENCHMARK_{provider.upper()}_{field.upper()}")
                        require(
                            bool(value),
                            f"Explicit BENCHMARK_{provider.upper()}_{field.upper()} is required",
                        )
                        if field == "url":
                            target = urlsplit(value)
                            require(
                                target.hostname in ("localhost", "127.0.0.1", "::1")
                                and target.scheme in ("http", "https")
                                and target.username is None
                                and not target.query,
                                "Providers must be local deployment URLs without credentials",
                            )
                        overrides[f"library_{provider}_{field}"] = value
                for key, value in overrides.items():
                    stack.enter_context(patch.object(settings, key, value))
                for module in (worker, processing_worker):
                    stack.enter_context(patch.object(module, "SessionLocal", self.sessions))
                # All native temporary files also belong to this benchmark, not OS temp.
                temporary = self.folder / "temporary"
                temporary.mkdir(exist_ok=True)
                stack.enter_context(patch.object(tempfile, "tempdir", str(temporary)))
                with self.sessions() as db:
                    user_id, project_id = (
                        uuid.UUID(self.state["user_id"]),
                        uuid.UUID(self.state["project_id"]),
                    )
                    if not db.get(User, user_id):
                        db.add(
                            User(
                                id=user_id,
                                username="benchmark",
                                email="benchmark@example.invalid",
                                password_hash="unused",
                                display_name="Isolated benchmark",
                            )
                        )
                        db.flush()
                        db.add(
                            Project(
                                id=project_id,
                                code="BENCH",
                                name="Disposable media benchmark",
                                owner_id=user_id,
                            )
                        )
                        db.flush()
                        db.add(Membership(project_id=project_id, user_id=user_id, role="admin"))
                        db.commit()
                self.base = f"/api/v1/projects/{project_id}/library"
                with TestClient(app) as client:
                    self.client = client
                    client.headers["Authorization"] = f"Bearer {create_access_token(str(user_id))}"
                    yield self
        finally:
            admin.dispose()

    def check_owner(self, conn):
        from sqlalchemy import text

        owner = conn.scalar(text(f'SELECT run_id FROM "{self.schema}"._benchmark_owner'))
        require(owner == self.state["run_id"], "Database ownership marker does not match")

    def request(self, method, path, **kwargs):
        response = self.client.request(method, self.base + path, **kwargs)
        require(response.status_code == 200, f"API {method} returned HTTP {response.status_code}")
        return response.json()

    def sources(self, entry):
        from app.core.config import settings
        from app.modules.generation import media_engine
        from app.modules.library.media_ingest import probe_media

        folder = self.folder / "sources"
        folder.mkdir(exist_ok=True)
        sources = self.state.setdefault("samples", [])
        seen = set()
        for index in range(self.plan["count"]):
            started = time.monotonic()
            path = (
                Path(self.plan["source_paths"][index])
                if self.plan["source_paths"]
                else folder / f"sample-{index:03}.mp4"
            )
            if not self.plan["source_paths"] and not path.exists():
                check_disk(self.folder, estimate_budget(self.plan))
                duration = self.plan["duration"]
                output = path.with_suffix(".partial.mp4")
                # Hue and tone change actual audiovisual content for every sample.
                media_engine.run(
                    [
                        "-f",
                        "lavfi",
                        "-i",
                        f"testsrc2=s=3840x2160:r=12:d={duration}",
                        "-f",
                        "lavfi",
                        "-i",
                        f"sine=frequency={330 + index * 37}:duration={duration}",
                        "-vf",
                        f"hue=h={index * 31}",
                        "-c:v",
                        "libx264",
                        "-threads",
                        "2",
                        "-preset",
                        "ultrafast",
                        "-b:v",
                        f"{self.plan['bitrate_mbps']}M",
                        "-maxrate",
                        f"{self.plan['bitrate_mbps']}M",
                        "-bufsize",
                        "4M",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "96k",
                        "-movflags",
                        "+faststart",
                        "-shortest",
                        str(output),
                    ],
                    timeout=max(180, duration * 20),
                )
                output.replace(path)
            require(path.is_file(), "Source is not a regular file")
            info = probe_media(path, "video")
            require(
                self.args.allow_heavy or info["duration_ms"] <= 30_000,
                "Long source needs --allow-heavy",
            )
            size = path.stat().st_size
            require(
                size <= settings.library_max_upload_bytes,
                "Source exceeds configured upload limit; no limits were bypassed",
            )
            digest = sha256(path)
            require(
                digest not in seen,
                "Duplicate source hash: repeated files are not independent videos",
            )
            seen.add(digest)
            sample = {
                "index": index,
                "path": str(path),
                "sha256": digest,
                "size_bytes": size,
                **info,
            }
            if len(sources) > index:
                require(sources[index]["sha256"] == digest, "Source changed since checkpoint")
                sample = {**sources[index], **sample}
                sources[index] = sample
            else:
                sources.append(sample)
            sample["source_check_seconds"] = round(time.monotonic() - started, 3)
            self.evidence.save()
        self.state["duration_gate"] = check_duration_gate(
            self.args, self.plan, sources, self.state.get("duration_gate")
        )
        self.evidence.save()
        budget = estimate_budget(self.plan, sources)
        entry["disk_admission"] = check_disk(self.folder, budget)
        entry["budget"] = budget
        for provider in self.plan["providers"]:
            required = budget["sidecar_peak_estimate_bytes"] + self.plan["reserve_bytes"]
            metrics = container_metrics([provider])
            disks = [d for item in metrics for d in item.get("disks", [])]
            require(
                bool(disks),
                "Cannot verify selected provider disk capacity; run on the local deployment",
            )
            require(
                all(d["free_bytes"] >= required for d in disks),
                "Insufficient provider disk capacity",
            )
            entry.setdefault("provider_disk_admission", {})[provider] = metrics

    def upload(self, entry):
        from sqlalchemy import select

        from app.models.library import LibraryMedia, MediaVersion

        for sample in self.state["samples"]:
            started = time.monotonic()
            require(sha256(sample["path"]) == sample["sha256"], "Source changed before upload")
            if not sample.get("version_id"):
                # Recover POST accepted before journal write without duplicate uploads.
                with self.sessions() as db:
                    matches = list(
                        db.scalars(
                            select(MediaVersion)
                            .join(LibraryMedia)
                            .where(
                                LibraryMedia.project_id == uuid.UUID(self.state["project_id"]),
                                MediaVersion.fingerprint == sample["sha256"],
                            )
                        )
                    )
                    require(len(matches) <= 1, "Ambiguous upload identity")
                    existing = str(matches[0].id) if matches else None
                result = (
                    self.request("GET", f"/versions/{existing}")
                    if existing
                    else self.request(
                        "POST",
                        "/uploads",
                        json={
                            "filename": Path(sample["path"]).name,
                            "size_bytes": sample["size_bytes"],
                            "fingerprint": sample["sha256"],
                        },
                    )
                )
                sample.update(version_id=result["id"], media_id=result["media_id"])
                self.evidence.save()
            path = f"/versions/{sample['version_id']}"
            version = self.request("GET", path)
            if version["status"] == "uploading":
                sample.setdefault("upload_resume_offsets", []).append(version["uploaded_bytes"])
                with Path(sample["path"]).open("rb") as source:
                    source.seek(version["uploaded_bytes"])
                    while chunk := source.read(version["chunk_size"]):
                        require(
                            shutil.disk_usage(self.folder).free
                            >= len(chunk) + self.plan["reserve_bytes"],
                            "Disk reserve reached; upload offset is retained",
                        )
                        version = self.request(
                            "POST",
                            path + f"/chunk?offset={version['uploaded_bytes']}",
                            files={"file": ("chunk", chunk)},
                        )
                self.request("POST", path + "/complete")
            sample["upload_seconds"] = round(time.monotonic() - started, 3)
            self.evidence.save()
        entry["sample_count"] = len(self.state["samples"])

    def preview(self, entry):
        from app.models.library import MediaVersion
        from app.modules.library import service, worker

        for sample in self.state["samples"]:
            started = time.monotonic()
            path = f"/versions/{sample['version_id']}"
            version = self.request("GET", path)
            source = service.staging_path(version["id"])
            held = source.with_suffix(".benchmark-held")
            if held.exists() and not source.exists():
                # A driver interruption during the explicit fault injection is reversible.
                held.replace(source)
                if version["status"] == "failed":
                    version = self.request("POST", path + "/retry")
            if (
                self.plan["exercise_recovery"]
                and not sample.get("recovery")
                and version["status"] == "queued"
            ):
                hidden = source.with_suffix(".benchmark-held")
                source.replace(hidden)
                try:
                    worker.process(uuid.UUID(version["id"]))
                    failed = self.request("GET", path)
                    require(failed["status"] == "failed", "Missing-staging fault was not reported")
                finally:
                    hidden.replace(source)
                self.request("POST", path + "/retry")

                def stop_after_original(version_id=version["id"]):
                    with self.sessions() as db:
                        return bool(db.get(MediaVersion, uuid.UUID(version_id)).original_hash)

                worker.process(uuid.UUID(version["id"]), canceled=stop_after_original)
                interrupted = self.request("GET", path)
                require(
                    interrupted["status"] == "queued",
                    "Interrupted worker did not preserve its queue entry",
                )
                require(
                    interrupted["original_hash"] == sample["sha256"],
                    "Cooperative interruption lost the persisted original",
                )
                sample["recovery"] = {
                    "missing_staging_reported": True,
                    "retry_accepted": True,
                    "cooperative_interruption_preserved_queue": True,
                    "persisted_original_survived_interruption": True,
                    "hard_kill_tested": False,
                }
                self.evidence.save()
            version = self.request("GET", path)
            if version["status"] == "failed" and self.args.retry_failed:
                self.request("POST", path + "/retry")
            worker.process(uuid.UUID(version["id"]))
            version = self.request("GET", path)
            require(
                version["status"] == "ready",
                "Preview failed; inspect isolated run, then --retry-failed",
            )
            require(version["original_hash"] == sample["sha256"], "Original hash changed")
            sample["preview_seconds"] = round(time.monotonic() - started, 3)
            sample["proxy_hash"] = version["proxy_hash"]
            self.evidence.save()
        entry["ready_count"] = len(self.state["samples"])

    def poll(self, run_id):
        from app.models.media_processing import MediaProcessingRun
        from app.modules.library import processing_worker

        deadline = time.monotonic() + self.args.timeout
        history = self.state.setdefault("provider_observations", {}).setdefault(run_id, [])
        while time.monotonic() < deadline:
            processing_worker.process(uuid.UUID(run_id))
            with self.sessions() as db:
                item = db.get(MediaProcessingRun, uuid.UUID(run_id))
                state = {
                    "status": item.status,
                    "phase": item.progress.get("phase"),
                    "attempts": item.attempts,
                }
            if (
                not history
                or history[-1]["status"] != state["status"]
                or history[-1]["phase"] != state["phase"]
            ):
                history.append({"time": now(), **state})
                self.state.setdefault("provider_observations", {})[run_id] = history
                self.evidence.save()
            if state["status"] in ("ready", "failed", "canceled"):
                require(
                    state["status"] == "ready",
                    "Provider did not finish successfully; state retained",
                )
                return
            time.sleep(2)
        raise BenchmarkError("Provider deadline exceeded; resume reconciles the same task ID")

    def provider(self, name, entry):
        from sqlalchemy import select

        from app.models.media_processing import MediaProcessingRun

        self.state["duration_gate"] = check_duration_gate(
            self.args, self.plan, self.state["samples"], self.state.get("duration_gate")
        )
        self.evidence.save()
        capabilities = ["preview"] if name == "mediacms" else ["visual"]
        for sample in self.state["samples"]:
            started = time.monotonic()
            runs = sample.setdefault("providers", {})
            if name not in runs:
                with self.sessions() as db:
                    prior = list(
                        db.scalars(
                            select(MediaProcessingRun).where(
                                MediaProcessingRun.version_id == uuid.UUID(sample["version_id"]),
                                MediaProcessingRun.provider == name,
                                MediaProcessingRun.operation == "process",
                            )
                        )
                    )
                    require(len(prior) <= 1, "Ambiguous provider task identity")
                    existing = str(prior[0].id) if prior else None
                if existing:
                    runs[name] = {"id": existing}
                else:
                    response = self.request(
                        "POST",
                        f"/versions/{sample['version_id']}/process",
                        json={"provider": name, "capabilities": capabilities},
                    )
                    runs[name] = {"id": response["id"]}
                self.evidence.save()
            run_id = runs[name]["id"]
            if self.args.retry_failed:
                listing = self.request(
                    "GET", f"/processing/runs?version_id={sample['version_id']}&limit=100"
                )
                current = next(r for r in listing["items"] if r["id"] == run_id)
                if current["status"] == "failed":
                    self.request("POST", f"/processing/runs/{run_id}/retry")
            self.poll(run_id)
            with self.sessions() as db:
                actual = db.get(MediaProcessingRun, uuid.UUID(run_id))
                runs[name]["capability_status"] = actual.outputs.get("capabilities", {})
                if name == "mediacms":
                    runs[name]["upstream_removed"] = actual.outputs.get("upstream_removed", False)
            runs[name]["elapsed_seconds"] = round(time.monotonic() - started, 3)
            runs[name]["ready"] = True
            self.evidence.save()
        entry["ready_count"] = len(self.state["samples"])

    def search(self, entry):
        allowed = {s["version_id"] for s in self.state["samples"]}
        marker = "benchmark-" + self.state["run_id"]
        # Explicit manual annotation measures exact-text retrieval, not ASR quality.
        for sample in self.state["samples"]:
            annotations = self.request("GET", f"/versions/{sample['version_id']}/annotations")
            items = annotations.get("items", []) if isinstance(annotations, dict) else annotations
            if not any(item["text"] == marker for item in items):
                self.request(
                    "POST",
                    f"/versions/{sample['version_id']}/annotations",
                    json={
                        "start_ms": 0,
                        "end_ms": min(1000, sample["duration_ms"]),
                        "kind": "visual",
                        "text": marker,
                    },
                )
        started = time.monotonic()
        exact = self.request(
            "POST", "/search", json={"query": marker, "mode": "annotated", "limit": 100}
        )
        require(
            {item["version_id"] for item in exact["items"]} == allowed,
            "Exact annotation retrieval missed owned samples",
        )
        entry["exact_annotation_search"] = {
            "seconds": round(time.monotonic() - started, 4),
            "hits": len(exact["items"]),
            "source": "manually seeded annotation; not ASR",
        }
        if "wise" in self.plan["providers"]:
            observations = []
            for _ in range(3):
                started = time.monotonic()
                result = self.request(
                    "POST",
                    "/search",
                    json={
                        "engine": "wise",
                        "target": "visual",
                        "query": self.plan["query"],
                        "limit": 100,
                    },
                )
                require(bool(result["items"]), "WISE returned no hits for indexed samples")
                require(
                    all(hit["version_id"] in allowed for hit in result["items"]),
                    "Search returned another project's media",
                )
                observations.append(
                    {
                        "seconds": round(time.monotonic() - started, 4),
                        "coverage": result["coverage"],
                        "hits": [
                            {
                                key: hit.get(key)
                                for key in ("version_id", "start_ms", "end_ms", "score")
                            }
                            for hit in result["items"]
                        ],
                    }
                )
            entry["wise_observations"] = observations
            entry["quality_claim"] = "None: throughput/scope only; no labeled relevance set"

    def verify(self, entry):
        from app.models.storage import Blob
        from app.modules.generation import media_engine
        from app.modules.library.media_ingest import input_options, probe_media
        from app.storage import cas

        observed = []
        for sample in self.state["samples"]:
            version = self.request("GET", f"/versions/{sample['version_id']}")
            require(version["status"] == "ready", "Version is not ready")
            require(
                sha256(sample["path"]) == sample["sha256"] == version["original_hash"],
                "Input or original hash changed",
            )
            with self.sessions() as db:
                original = db.get(Blob, version["original_hash"])
                with cas.local_file(original) as path:
                    require(sha256(path) == sample["sha256"], "CAS original differs from source")
                proxy = db.get(Blob, version["proxy_hash"])
                with cas.local_file(proxy) as path:
                    info = probe_media(path, "video")
                    require(
                        abs(info["duration_ms"] - sample["duration_ms"]) <= 1500,
                        "Preview duration mismatch",
                    )
                    media_engine.run(
                        [*input_options(info), "-xerror", "-i", str(path), "-f", "null", "-"],
                        timeout=max(180, sample["duration_ms"] / 1000 * 4),
                    )
                    observed.append(
                        {
                            "version_id": version["id"],
                            "source_sha256": sample["sha256"],
                            "cas_sha256_verified": True,
                            "proxy_sha256": sha256(path),
                            "proxy_duration_ms": info["duration_ms"],
                            "full_proxy_decode": True,
                        }
                    )
        entry["samples"] = observed
        self.state["verified"] = True

    def cleanup(self, entry):
        from sqlalchemy import select
        from sqlalchemy.schema import DropSchema

        from app.models.library import LibraryMedia, MediaVersion
        from app.models.media_processing import MediaProcessingRun

        # Query the dedicated schema, recovering rows accepted before a journal save.
        with self.sessions() as db:
            active = list(
                db.scalars(
                    select(MediaProcessingRun).where(
                        MediaProcessingRun.operation == "process",
                        MediaProcessingRun.status.in_(("queued", "waiting", "processing")),
                    )
                )
            )
            active_ids = [str(run.id) for run in active]
        for run_id in active_ids:
            self.request("POST", f"/processing/runs/{run_id}/cancel")
            deadline = time.monotonic() + self.args.timeout
            from app.modules.library import processing_worker

            while time.monotonic() < deadline:
                processing_worker.process(uuid.UUID(run_id))
                with self.sessions() as db:
                    status = db.get(MediaProcessingRun, uuid.UUID(run_id)).status
                if status in ("ready", "failed", "canceled"):
                    break
                time.sleep(2)
            else:
                raise BenchmarkError(
                    "Owned task still active; cleanup retained all recovery records"
                )
        with self.sessions() as db:
            rows = list(db.scalars(select(LibraryMedia)))
            trash_ids = [str(row.id) for row in rows if row.deleted_at is None]
            unfinished = [
                (str(v.id), v.status)
                for v in db.scalars(select(MediaVersion))
                if v.status in ("uploading", "failed")
            ]
        for version_id, _ in unfinished:
            self.request("POST", f"/versions/{version_id}/cancel")
        # Queued imports have no remote jobs and are safe to cancel in this owned schema.
        with self.sessions() as db:
            for v in db.scalars(
                select(MediaVersion).where(MediaVersion.status.in_(("queued", "processing")))
            ):
                v.status = "canceled"
            db.commit()
        for media_id in trash_ids:
            self.request("POST", f"/{media_id}/trash")
        with self.sessions() as db:
            pending = [
                str(r.id)
                for r in db.scalars(
                    select(MediaProcessingRun).where(
                        MediaProcessingRun.operation == "delete",
                        MediaProcessingRun.status != "ready",
                    )
                )
            ]
        for run_id in pending:
            with self.sessions() as db:
                failed = db.get(MediaProcessingRun, uuid.UUID(run_id)).status == "failed"
            if failed:
                self.request("POST", f"/processing/runs/{run_id}/retry")
            self.poll(run_id)
        with self.admin.begin() as conn:
            self.check_owner(conn)
            conn.execute(DropSchema(self.schema, cascade=True))
        entry["owned_schema_dropped"] = True
        self.state["schema_dropped"] = True
        self.evidence.save()
        # Do not delete externally supplied sources or the evidence itself.
        for name in ("sources", "cas", "staging", "transfer", "temporary"):
            target = self.folder / name
            require(
                not target.is_symlink() and target.resolve().parent == self.folder,
                "Unsafe cleanup child",
            )
            if target.exists():
                shutil.rmtree(target)
        entry["external_source_files_removed"] = 0
        self.state["cleaned"] = True


def open_evidence(args):
    require(args.concurrency == 1, "Only concurrency=1 is supported")
    require(10 <= args.timeout <= 86400, "Timeout must be 10..86400s")
    require(not (args.phase and args.until), "Choose phase OR until")
    if args.resume:
        folder = run_folder(args.root, args.resume)
        state = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
        require(
            state["format"] == FORMAT and state["run_id"] == args.resume,
            "Evidence identity mismatch",
        )
        require(not state.get("cleaned"), "Run is already cleaned; evidence remains available")
        require(
            not state["plan"]["providers"] or args.allow_external,
            "Resume with --allow-external to contact its providers",
        )
        if args.phase != "cleanup":
            state["duration_gate"] = check_duration_gate(
                args, state["plan"], state.get("samples") or None, state.get("duration_gate")
            )
        return Evidence(folder, state)
    plan = validate_plan(args)
    duration_gate = check_duration_gate(args, plan)
    if args.count > 1:
        require(bool(args.previous_run), "Larger tier requires --previous-run evidence")
        prior = json.loads(
            (run_folder(args.root, args.previous_run) / "evidence.json").read_text(encoding="utf-8")
        )
        require(
            prior.get("verified") and prior["plan"]["count"] == TIERS[TIERS.index(args.count) - 1],
            "Previous tier has not been verified",
        )
        require(
            prior.get("cleaned") and prior.get("schema_dropped"),
            "Previous tier must be fully cleaned and its owned schema dropped",
        )
        require(
            prior["plan"]["providers"] == plan["providers"],
            "Previous tier used different providers",
        )
        require(
            prior["plan"]["duration"] >= plan["duration"]
            and prior["plan"]["bitrate_mbps"] >= plan["bitrate_mbps"]
            and bool(prior["plan"]["source_paths"]) == bool(plan["source_paths"]),
            "Previous tier used a lighter or different source workload",
        )
    run_id = uuid.uuid4().hex
    folder = run_folder(args.root, run_id)
    folder.mkdir(parents=True)
    state = {
        "format": FORMAT,
        "run_id": run_id,
        "created_at": now(),
        "user_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "plan": plan,
        "duration_gate": duration_gate,
        "attempts": [],
        "completed": [],
        "samples": [],
        "limitations": [
            "Synthetic low-bitrate 4K is not representative of high-bitrate camera footage",
            "ASGI in-process transport; no browser/TLS/WAN/MinIO throughput measurement",
            "Isolated PostgreSQL schema and filesystem CAS; sidecar resources may be shared",
            "Sampled RSS and disk, not guaranteed exact peaks; RSS sums can include shared pages",
            "GPU samples cover whole devices including other processes; "
            "not workload attribution or exact peaks",
            "No automatic server kill, GPU correctness, ASR accuracy or semantic recall claim",
            "Original workload limits remain enforced; this tool does not raise service quotas",
        ],
    }
    evidence = Evidence(folder, state)
    evidence.save()
    return evidence


def execute(args):
    evidence = None
    try:
        evidence = open_evidence(args)
        print(json.dumps({"run_id": evidence.state["run_id"], "evidence": str(evidence.path)}))
        plan = evidence.state["plan"]
        require(
            args.phase not in ("wise", "mediacms") or args.phase in plan["providers"],
            "Provider was not included in this run's explicit plan",
        )
        if not args.resume:
            with evidence.phase("admission") as entry:
                entry["budget"] = estimate_budget(plan)
                entry["disk"] = check_disk(evidence.folder, entry["budget"])
        selected = list(PHASES) if not args.phase else [args.phase]
        if args.until:
            selected = selected[: selected.index(args.until) + 1]
        selected = [p for p in selected if p not in ("wise", "mediacms") or p in plan["providers"]]
        pipeline = Pipeline(evidence, args)
        with pipeline.connect():
            for name in selected:
                if name in evidence.state["completed"]:
                    continue
                if name != "cleanup":
                    required = [
                        p
                        for p in PHASES[: PHASES.index(name)]
                        if p not in ("mediacms", "wise") or p in plan["providers"]
                    ]
                    require(
                        all(p in evidence.state["completed"] for p in required),
                        "Run the preceding phases first",
                    )
                with evidence.phase(name) as entry:
                    if name in ("wise", "mediacms"):
                        pipeline.provider(name, entry)
                    else:
                        getattr(pipeline, name)(entry)
                print(
                    json.dumps(
                        {"phase": name, "status": "passed", "seconds": entry["elapsed_seconds"]}
                    )
                )
        return 0
    except BaseException as exc:
        message = str(exc) if isinstance(exc, BenchmarkError) else type(exc).__name__
        print(
            json.dumps(
                {
                    "status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                    "reason": message,
                    "evidence": str(evidence.path) if evidence else None,
                }
            )
        )
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


def main(argv=None):
    args = parse_args(argv)
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)  # Suppress URL/DSN-bearing third-party diagnostic logs.
    try:
        with exclusive_run(args.root):
            return execute(args)
    except BenchmarkError as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}))
        return 1
    finally:
        logging.disable(previous_logging)


if __name__ == "__main__":
    raise SystemExit(main())
