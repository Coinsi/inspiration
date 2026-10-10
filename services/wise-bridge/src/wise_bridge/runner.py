import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from wise_bridge import MODELS
from wise_bridge.optional_models import (
    OptionalModelUnavailable,
    inspect_optional,
    require_optional,
)
from wise_bridge.query_process import QueryWorkerError, ResidentQueryProcess

logger = logging.getLogger(__name__)


class Canceled(Exception):
    pass


class EngineFailure(Exception):
    pass


def validate_index_budget(engine_dir, settings):
    size = sum(path.stat().st_size for path in engine_dir.rglob("*.faiss"))
    if size > settings.query_cache_index_bytes:
        raise EngineFailure(
            "Project index exceeds WISE_QUERY_CACHE_INDEX_BYTES; reduce project size "
            "or increase the query cache budget with sufficient container memory"
        )


class ProcessRunner:
    """Bounded child processes; logs stay private and never enter HTTP errors."""

    def __init__(self, settings):
        self.settings = settings
        self.query_process = ResidentQueryProcess(settings)

    def close_queries(self):
        self.query_process.close()

    def run(self, args, directory, canceled, timeout=None):
        directory.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONPATH"] = (
            str(Path(__file__).resolve().parents[1])
            + os.pathsep
            + env.get("PYTHONPATH", "")
        )
        env.setdefault("OMP_NUM_THREADS", "2")
        env.setdefault("MKL_NUM_THREADS", "2")
        env.setdefault(
            "FEATURE_EXTRACTOR_CONFIG", '{"default":{"compile":false,"warmup":false}}'
        )
        kwargs = (
            {"start_new_session": True}
            if os.name != "nt"
            else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        )
        limit = timeout or self.settings.process_timeout_seconds
        started = time.monotonic()
        last_storage_check = started - 10
        with (directory / "engine.log").open("ab") as log:
            process = subprocess.Popen(
                [self.settings.python, *args],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=directory,
                **kwargs,
            )
            try:
                while process.poll() is None:
                    if canceled():
                        raise Canceled()
                    if time.monotonic() - started > limit:
                        raise EngineFailure(
                            "WISE processing exceeded the configured time limit"
                        )
                    if (
                        shutil.disk_usage(self.settings.data_dir).free
                        < self.settings.min_free_bytes
                    ):
                        raise EngineFailure("Insufficient free processing disk space")
                    if time.monotonic() - last_storage_check > 5:
                        last_storage_check = time.monotonic()
                        used = sum(
                            p.stat().st_size
                            for p in self.settings.data_dir.rglob("*")
                            if p.is_file()
                        )
                        if used > self.settings.max_storage_bytes:
                            raise EngineFailure(
                                "Configured processing storage limit reached"
                            )
                        if (
                            directory / "engine.log"
                        ).stat().st_size > self.settings.max_engine_log_bytes:
                            raise EngineFailure(
                                "WISE engine log exceeded service limit"
                            )
                    time.sleep(0.2)
                if canceled():
                    raise Canceled()
                if process.returncode:
                    raise EngineFailure(
                        "WISE engine failed; inspect the private job log"
                    )
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            check=False,
                        )
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)

    def operation(
        self, operation, payload, directory, canceled=lambda: False, timeout=None
    ):
        if operation == "search":
            try:
                return self.query_process.request(payload, canceled, timeout)
            except QueryWorkerError as exc:
                raise EngineFailure(str(exc)) from exc
        directory.mkdir(parents=True, exist_ok=True)
        request = directory / (str(uuid.uuid4()) + ".request.json")
        output = request.with_suffix(".output.json")
        request.write_text(json.dumps(payload), encoding="utf-8")
        try:
            self.run(
                [
                    "-m",
                    "wise_bridge.upstream_worker",
                    operation,
                    str(request),
                    str(output),
                ],
                directory,
                canceled,
                timeout,
            )
            return json.loads(output.read_text(encoding="utf-8"))
        finally:
            request.unlink(missing_ok=True)
            output.unlink(missing_ok=True)

    def build(self, sources, snapshot, previous, phase, canceled, operation="upsert"):
        if operation not in {"upsert", "delete"}:
            raise EngineFailure("Invalid snapshot build operation")
        engine_dir = snapshot / "engine"
        snapshot.mkdir(parents=True, exist_ok=True)
        capabilities = sorted(
            {cap for value in sources.values() for cap in value["capabilities"]}
        )
        kinds = sorted({value["media_type"] for value in sources.values()})
        old = (
            json.loads((previous / "manifest.json").read_text(encoding="utf-8"))
            if previous and (previous / "manifest.json").exists()
            else None
        )
        old_signature = old.get("signature", {}) if old else {}
        old_models = old_signature.get("models", {})
        old_optional = old_signature.get("optional_models", {})
        optional_caps = {"face", "object"}.intersection(capabilities)
        deleting = operation == "delete"
        # The feature identity binds exact weights, dependencies, effective
        # extractor configuration and implementation, without a per-probe nonce.
        # Model name or the file manifest alone cannot identify these vectors.
        # A delete copies historical signatures without reading possibly removed
        # model files: pruning does not infer anything with those weights.
        optional = (
            {cap: old_optional.get(cap) for cap in optional_caps}
            if deleting
            else {
                cap: inspect_optional(cap, self.settings).get("feature_fingerprint")
                for cap in optional_caps
            }
        )
        signature = {
            "capabilities": capabilities,
            "kinds": kinds,
            "models": {
                cap: old_models.get(cap, MODELS[cap]) if deleting else MODELS[cap]
                for cap in capabilities
            },
            "optional_models": optional,
        }
        # Source payload includes immutable content hash/path, kind and requested
        # capabilities. Reuse only exact matches with the same pinned models.
        # Restored generations cannot enter this set until their deletion has
        # published a HEAD without them (enforced by Store's restore protocol).
        retained = {}
        if sources and old and (previous / "engine").is_dir():
            retained = {
                sid: value
                for sid, value in sources.items()
                if old["sources"].get(sid) == value
                and all(
                    old_models.get(cap) == signature["models"][cap]
                    for cap in value["capabilities"]
                )
                and all(
                    deleting
                    or (optional[cap] and old_optional.get(cap) == optional[cap])
                    for cap in set(value["capabilities"]) & optional_caps
                )
            }
        todo = {sid: value for sid, value in sources.items() if sid not in retained}
        if deleting and todo:
            raise EngineFailure(
                "Cleanup needs the published feature snapshot; restore it before retrying"
            )
        extraction_caps = sorted(
            {cap for value in todo.values() for cap in value["capabilities"]}
        )
        # Missing optional weights must block new inference, but cannot prevent
        # pruning already stored features during source deletion.
        try:
            require_optional(extraction_caps, self.settings)
        except OptionalModelUnavailable as exc:
            raise EngineFailure(str(exc)) from exc
        if retained:
            phase("preparing")
            for path in [previous / "engine", *(previous / "engine").rglob("*")]:
                if path.resolve() != path.absolute():
                    raise EngineFailure("Feature snapshot path failed validation")

            def copy_feature(source, destination):
                if canceled():
                    raise Canceled()
                if Path(source).is_symlink():
                    raise EngineFailure(
                        "Feature snapshot cannot contain symbolic links"
                    )
                return shutil.copy2(source, destination)

            shutil.copytree(previous / "engine", engine_dir, copy_function=copy_feature)
            self.operation(
                "prune",
                {
                    "project_dir": str(engine_dir),
                    "expected_sources": list(old["sources"]),
                    "retained_sources": list(retained),
                },
                snapshot,
                canceled,
            )
        if not sources:
            inspection = {"media_map": {}, "counts": {}, "models": {}}
        else:
            if todo:
                # WISE add-media ignores newly supplied feature extractors and
                # cannot add a previously absent modality. Always extract only
                # changed/new sources into a fresh project, then use the actual
                # upstream merge. Previously indexed sources never re-encode.
                extraction_dir = snapshot / "delta" if retained else engine_dir
                listing = snapshot / "sources.txt"
                # Path origin is our trusted upload store; names are UUIDs, never
                # supplied paths or URLs. WISE receives only this explicit list.
                listing.write_text(
                    "".join(
                        str(Path(value["file"]).relative_to(self.settings.data_dir))
                        + "\n"
                        for value in todo.values()
                    ),
                    encoding="utf-8",
                )
                command = [
                    "-m",
                    "wise",
                    "extract-features",
                    str(self.settings.data_dir),
                    "--media-files-from",
                    str(listing),
                    "--project-dir",
                    str(extraction_dir),
                    "--num-workers",
                    "0",
                    "--shard-maxcount",
                    "4096",
                    "--no-thumbnails",
                    "--yes",
                ]
                for cap in extraction_caps:
                    if cap == "audio":
                        command += ["--audio-feature-id", MODELS[cap]]
                    else:
                        command += [
                            "--image-feature-id",
                            MODELS[cap],
                            "--video-feature-id",
                            MODELS[cap],
                        ]
                phase("extracting")
                if {"face", "object"}.intersection(extraction_caps):
                    command = [
                        "-m",
                        "wise_bridge.offline_entry",
                        json.dumps(extraction_caps),
                        "--",
                        *command[2:],
                    ]
                self.run(command, snapshot, canceled)
                if retained:
                    phase("indexing")
                    self.operation(
                        "merge",
                        {
                            "project_dir": str(engine_dir),
                            "delta_dir": str(extraction_dir),
                            "retained_sources": list(retained),
                            "delta_sources": list(todo),
                        },
                        snapshot,
                        canceled,
                    )
                    # This exact fresh child belongs to our registered,
                    # unpublished snapshot. No other snapshots are touched.
                    if extraction_dir.resolve() != extraction_dir.absolute():
                        raise EngineFailure(
                            "Feature delta cleanup path failed validation"
                        )
                    shutil.rmtree(extraction_dir)
            phase("indexing")
            self.run(
                [
                    "-m",
                    "wise",
                    "create-index",
                    "--project-dir",
                    str(engine_dir),
                    "--index-type",
                    "IndexFlatIP",
                    "--overwrite",
                ],
                snapshot,
                canceled,
            )
            # Inspect opens real indices too; reject an unqueryable revision
            # before loading those files and before replacing the current HEAD.
            validate_index_budget(engine_dir, self.settings)
            phase("publishing")
            inspection = self.operation(
                "inspect",
                {"project_dir": str(engine_dir), "source_ids": list(sources)},
                snapshot,
                canceled,
            )
        if not deleting and any(
            not fingerprint
            or inspect_optional(cap, self.settings).get("feature_fingerprint")
            != fingerprint
            for cap, fingerprint in optional.items()
        ):
            raise EngineFailure(
                "Optional model fingerprint changed during build; "
                "retry after restoring the model installation"
            )
        manifest = {
            "signature": signature,
            "sources": sources,
            "inspection": inspection,
            "build": {"reused_sources": len(retained), "extracted_sources": len(todo)},
        }
        (snapshot / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return inspection


class Worker:
    def __init__(self, settings, store, runner=None):
        self.settings, self.store = settings, store
        self.runner = runner or ProcessRunner(settings)
        self.engine_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.verified = False
        self.probe_error = None
        self.lease = None

    def start(self):
        # Reject multiple ASGI workers instead of double-claiming/recovering jobs.
        self.lease = (self.settings.data_dir / "worker.lock").open("a+b")
        if os.name == "nt":
            import msvcrt

            self.lease.seek(0)
            self.lease.write(b"0")
            self.lease.flush()
            self.lease.seek(0)
            msvcrt.locking(self.lease.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.store.recover()
        self.thread = threading.Thread(
            target=self.loop, name="wise-build-worker", daemon=True
        )
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=15)
            if self.thread.is_alive():
                # Keep the OS lease until process exit rather than let another
                # process recover a job that is still copying/extracting here.
                raise RuntimeError("WISE worker is still shutting down")
        # A concurrent search observes stop_event and terminates its child first.
        if not self.engine_lock.acquire(timeout=15):
            raise RuntimeError("WISE query is still shutting down")
        try:
            if hasattr(self.runner, "close_queries"):
                self.runner.close_queries()
        finally:
            self.engine_lock.release()
        if self.lease:
            self.lease.close()

    def loop(self):
        interrupted = None
        while not self.stop_event.is_set():
            job = None
            try:
                if interrupted:
                    # If the database was temporarily unavailable when handling
                    # an error, reconcile that claim before accepting more work.
                    self.store.finish(
                        interrupted, "failed", "WISE worker failed; retry the task"
                    )
                    interrupted = None
                job = self.store.claim()
                if job:
                    self.process(job)
                else:
                    self.wake.wait(1)
                    self.wake.clear()
            except Exception:
                logger.exception("WISE worker operation failed")
                interrupted = job["id"] if job else interrupted
                self.stop_event.wait(1)

    def process(self, job):
        project_id = job["project_id"]
        revision = str(uuid.uuid4())
        snapshot = (
            self.settings.data_dir / "projects" / project_id / "snapshots" / revision
        )

        def canceled():
            return (
                self.stop_event.is_set()
                or self.store.job(job["id"])["state"] == "canceled"
            )

        with self.engine_lock:
            try:
                # Serial builds never overlap the resident model/index heap.
                # Closing before any snapshot work also releases Windows file
                # handles before old revisions can be deleted after publication.
                if hasattr(self.runner, "close_queries"):
                    self.runner.close_queries()
                # A killed process cannot execute finally. Registered incomplete
                # snapshots must be reclaimed before the probe/storage checks.
                self._cleanup_unpublished_snapshots()
                head = self.store.head(project_id)
                previous = snapshot.parent / head if head else None
                sources = self.store.sources(project_id)
                if job["kind"] == "upsert":
                    if job["source_id"] not in self.store.sources(
                        project_id, active_only=False
                    ):
                        raise Canceled()
                    existing = sources.get(job["source_id"], {})
                    sources[job["source_id"]] = {
                        **job["payload"],
                        "capabilities": sorted(
                            set(existing.get("capabilities", []))
                            | set(job["payload"]["capabilities"])
                        ),
                    }
                # A snapshot duplicates feature files, so reserve room before copy.
                used = sum(
                    p.stat().st_size
                    for p in self.settings.data_dir.rglob("*")
                    if p.is_file()
                )
                previous_size = (
                    sum(p.stat().st_size for p in previous.rglob("*") if p.is_file())
                    if sources and previous and previous.exists()
                    else 0
                )
                if sources and used + previous_size >= self.settings.max_storage_bytes:
                    raise EngineFailure("Configured processing storage limit reached")
                if (
                    shutil.disk_usage(self.settings.data_dir).free
                    < previous_size + self.settings.min_free_bytes
                ):
                    raise EngineFailure(
                        "Insufficient free space for an isolated index snapshot"
                    )
                # Empty deletion snapshots need no WISE process, model or copy.
                # This also lets deletion free an over-budget project safely.
                if sources:
                    self.runner.operation(
                        "probe", {}, self.settings.data_dir / "runtime", canceled, 60
                    )
                    self.verified = True
                    self.probe_error = None
                if not self.store.begin_snapshot(job, revision):
                    raise Canceled()
                result = self.runner.build(
                    sources,
                    snapshot,
                    previous,
                    lambda phase: self.store.set_phase(job["id"], phase),
                    canceled,
                    job["kind"],
                )
                if canceled():
                    raise Canceled()
                validate_index_budget(snapshot / "engine", self.settings)
                result["revision"] = revision
                published = self.store.publish(
                    job, revision, result, sources.get(job["source_id"])
                )
                if published:
                    # Search shares engine_lock, so old snapshots have no readers.
                    # Keep the previous published revision as a bounded recovery aid.
                    self._cleanup_snapshots(
                        project_id,
                        {revision, head} if job["kind"] == "upsert" else {revision},
                    )
                    self._cleanup_deleted_sources(project_id)
                else:
                    self._cleanup_unpublished_snapshots()
            except Canceled:
                # Shutdown jobs remain processing for recovery on the next start.
                if not self.stop_event.is_set():
                    self.store.finish(job["id"], "canceled")
            except Exception as exc:
                logger.exception("WISE task %s failed", job["id"])
                self.probe_error = "WISE unavailable or processing failed"
                safe_error = (
                    str(exc)
                    if isinstance(exc, EngineFailure)
                    else "WISE integration failed; inspect the private job log"
                )
                self.store.finish(job["id"], "failed", safe_error)
            finally:
                # Diagnostics and cleanup must not kill the only queue consumer.
                # Failed cleanup retains its ownership row for the next attempt.
                try:
                    log = snapshot / "engine.log"
                    if log.exists():
                        log_dir = self.settings.data_dir / "logs"
                        log_dir.mkdir(exist_ok=True)
                        shutil.copyfile(log, log_dir / (job["id"] + ".log"))
                except OSError:
                    logger.exception("Could not preserve WISE task log %s", job["id"])
                try:
                    self._cleanup_unpublished_snapshots()
                except Exception:
                    logger.exception("Could not clean incomplete WISE snapshots")

    def _remove_snapshot(self, record):
        # Registry provenance alone must not permit path traversal or following a
        # replaced directory/junction outside the managed snapshot tree.
        project_id = str(uuid.UUID(record["project_id"]))
        revision = str(uuid.UUID(record["revision"]))
        base = self.settings.data_dir.resolve()
        path = base / "projects" / project_id / "snapshots" / revision
        if path.resolve() != path or path.is_symlink():
            raise EngineFailure("Snapshot cleanup path failed validation")
        if path.exists():
            shutil.rmtree(path)
        self.store.forget_snapshot(revision)

    def _cleanup_unpublished_snapshots(self):
        for record in self.store.snapshots(unpublished=True):
            self._remove_snapshot(record)

    def _cleanup_snapshots(self, project_id, keep):
        for record in self.store.snapshots(project_id=project_id):
            if record["revision"] not in keep:
                self._remove_snapshot(record)

    def _cleanup_deleted_sources(self, project_id):
        # Only remove files after no retained snapshot references them.
        base = self.settings.data_dir / "projects" / project_id
        referenced = set()
        for manifest in (base / "snapshots").glob("*/manifest.json"):
            referenced.update(
                value["file"]
                for value in json.loads(manifest.read_text(encoding="utf-8"))[
                    "sources"
                ].values()
            )
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT payload FROM sources WHERE project_id=? AND deleted=0",
                (project_id,),
            ).fetchall()
            referenced.update(json.loads(row["payload"])["file"] for row in active)
            pending = conn.execute(
                "SELECT j.payload FROM jobs j JOIN sources s ON s.project_id=j.project_id AND s.id=j.source_id WHERE j.project_id=? AND j.kind='upsert' AND j.state IN ('queued','processing') AND s.deleted=0 AND s.generation=j.generation",
                (project_id,),
            ).fetchall()
            referenced.update(json.loads(row["payload"])["file"] for row in pending)
            for path in (self.settings.data_dir / "sources" / project_id).glob("*"):
                if (
                    path.is_file()
                    and str(path) not in referenced
                    and path.suffix != ".upload"
                ):
                    path.unlink()

    def _query_fingerprint(self, cap, manifest):
        if cap not in {"face", "object"}:
            return None
        state = inspect_optional(cap, self.settings)
        fingerprint = state.get("feature_fingerprint")
        if not state.get("ready") or not fingerprint:
            raise EngineFailure(
                "Optional model acceptance is unavailable; "
                "complete the offline probe before searching"
            )
        signature = manifest.get("signature", {})
        if (
            signature.get("optional_models", {}).get(cap) != fingerprint
            or signature.get("models", {}).get(cap) != MODELS[cap]
        ):
            raise EngineFailure(
                "Optional index fingerprint is outdated or unknown; "
                "rebuild this project's index before searching"
            )
        return fingerprint

    def search(self, project_id, request):
        if not self.engine_lock.acquire(timeout=1):
            raise EngineFailure("WISE is processing another task; retry search shortly")
        try:
            head = self.store.head(project_id)
            allowed = set(self.store.sources(project_id))
            if request.get("source_ids") is not None:
                allowed.intersection_update(request["source_ids"])
            if not head or not allowed:
                return {"revision": head, "items": [], "has_more": False}
            snapshot = (
                self.settings.data_dir / "projects" / project_id / "snapshots" / head
            )
            manifest = json.loads(
                (snapshot / "manifest.json").read_text(encoding="utf-8")
            )
            cap = request["modality"]
            allowed = {
                sid
                for sid in allowed
                if manifest["inspection"]["counts"].get(sid, {}).get(cap, 0)
            }
            if not allowed:
                return {"revision": head, "items": [], "has_more": False}
            fingerprint = self._query_fingerprint(cap, manifest)
            payload = {
                **request,
                "source_ids": sorted(allowed),
                "project_dir": str(snapshot / "engine"),
                "project_id": project_id,
                "revision": head,
                # Internal value comes from verified state, never client input.
                "feature_fingerprint": fingerprint,
            }
            rows = self.runner.operation(
                "search",
                payload,
                self.settings.data_dir / "runtime",
                self.stop_event.is_set,
                self.settings.search_timeout_seconds,
            )
            # An operator may replace/revalidate model files while inference is
            # running. Never return results from a now-unmatched feature space.
            if self._query_fingerprint(cap, manifest) != fingerprint:
                raise EngineFailure(
                    "Optional model fingerprint changed during search; "
                    "rebuild the index before retrying"
                )
            # A deletion can be queued while FAISS runs; recheck at response time.
            visible = set(self.store.sources(project_id)) & allowed
            return {
                "revision": head,
                "items": [row for row in rows if row["source_id"] in visible],
                "has_more": len(rows) >= request["limit"],
            }
        finally:
            self.engine_lock.release()
