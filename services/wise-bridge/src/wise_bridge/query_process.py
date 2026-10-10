"""One serial, supervised query process. No ML imports in the HTTP process."""

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from wise_bridge.query_errors import REFERENCE_ERRORS, ReferenceQueryError

MAX_MESSAGE_BYTES = 4 * 1024**2


class QueryWorkerError(Exception):
    pass


class ResidentQueryProcess:
    def __init__(self, settings, command=None):
        self.settings = settings
        self.command = command or [
            settings.python,
            "-m",
            "wise_bridge.upstream_worker",
            "serve",
        ]
        self.process = None
        self.log = None
        self.reader = None
        self.writer = None
        self.responses = None
        self.models = set()
        self.feature_fingerprints = {}

    def _start(self):
        directory = self.settings.data_dir / "runtime"
        directory.mkdir(parents=True, exist_ok=True)
        log_path = directory / "query-worker.log"
        if (
            log_path.exists()
            and log_path.stat().st_size >= self.settings.max_engine_log_bytes
        ):
            log_path.replace(directory / "query-worker.previous.log")
        self.log = log_path.open("ab")
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
        env["WISE_QUERY_CACHE_MODELS"] = str(self.settings.query_cache_models)
        env["WISE_QUERY_CACHE_PROJECTS"] = str(self.settings.query_cache_projects)
        env["WISE_QUERY_CACHE_INDEX_BYTES"] = str(self.settings.query_cache_index_bytes)
        kwargs = (
            {"start_new_session": True}
            if os.name != "nt"
            else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        )
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            env=env,
            cwd=directory,
            **kwargs,
        )
        responses = self.responses = queue.Queue(maxsize=2)
        process = self.process

        def read_responses():
            try:
                while True:
                    line = process.stdout.readline(MAX_MESSAGE_BYTES + 1)
                    if (
                        not line
                        or len(line) > MAX_MESSAGE_BYTES
                        or not line.endswith(b"\n")
                    ):
                        raise ValueError("Invalid response frame")
                    responses.put_nowait(json.loads(line))
            except (OSError, ValueError, queue.Full):
                try:
                    responses.put_nowait(None)
                except queue.Full:
                    pass

        self.reader = threading.Thread(
            target=read_responses, daemon=True, name="wise-query-reader"
        )
        self.reader.start()

    def request(self, payload, canceled=lambda: False, timeout=None):
        # MultiModalModel uses a process-global functools.cache. Restarting when
        # the model budget is exhausted reliably releases those upstream refs.
        model = payload.get("modality")
        fingerprint = payload.get("feature_fingerprint")
        if model in {"face", "object"} and (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in fingerprint)
        ):
            self.close()
            raise QueryWorkerError(
                "Optional query fingerprint is missing; rebuild the index before searching"
            )
        if self.process and (
            self.process.poll() is not None
            or model in self.models
            and self.feature_fingerprints.get(model) != fingerprint
            or model not in self.models
            and len(self.models) >= self.settings.query_cache_models
        ):
            self.close()
        request_id = str(uuid.uuid4())
        packet = (
            json.dumps({"request_id": request_id, "payload": payload}) + "\n"
        ).encode()
        if len(packet) > MAX_MESSAGE_BYTES:
            raise QueryWorkerError("WISE query request exceeds the service limit")
        started = time.monotonic()
        deadline = started + (
            timeout if timeout is not None else self.settings.search_timeout_seconds
        )
        try:
            if canceled():
                raise QueryWorkerError("WISE query was canceled")
            if self.process is None:
                self._start()
            process, responses = self.process, self.responses
            write_failed = threading.Event()

            def send_request():
                try:
                    process.stdin.write(packet)
                    process.stdin.flush()
                except (OSError, ValueError):
                    write_failed.set()

            self.writer = threading.Thread(
                target=send_request, daemon=True, name="wise-query-writer"
            )
            self.writer.start()
            while True:
                if canceled():
                    raise QueryWorkerError("WISE query was canceled")
                if time.monotonic() >= deadline:
                    raise QueryWorkerError(
                        "WISE query exceeded the configured time limit"
                    )
                if write_failed.is_set():
                    raise QueryWorkerError("WISE query process disconnected")
                if (
                    shutil.disk_usage(self.settings.data_dir).free
                    < self.settings.min_free_bytes
                ):
                    raise QueryWorkerError("Insufficient free processing disk space")
                if (
                    os.fstat(self.log.fileno()).st_size
                    > self.settings.max_engine_log_bytes
                ):
                    raise QueryWorkerError("WISE query log exceeded the service limit")
                try:
                    response = responses.get(
                        timeout=min(0.1, max(0.001, deadline - time.monotonic()))
                    )
                except queue.Empty:
                    if process.poll() is not None:
                        raise QueryWorkerError(
                            "WISE query process exited unexpectedly"
                        ) from None
                    continue
                if (
                    not isinstance(response, dict)
                    or response.get("request_id") != request_id
                ):
                    raise QueryWorkerError(
                        "WISE query process returned an invalid response"
                    )
                if "input_error" in response:
                    code = response["input_error"]
                    if (
                        set(response) != {"request_id", "input_error"}
                        or not isinstance(code, str)
                        or code not in REFERENCE_ERRORS
                        or model != "object"
                        or not payload.get("query_file")
                    ):
                        raise QueryWorkerError(
                            "WISE query process returned an invalid response"
                        )
                    # Extraction completed normally but the reference contained
                    # no selectable object. Account for the loaded encoder and
                    # keep this healthy process for the user's next query.
                    self.models.add(model)
                    self.feature_fingerprints[model] = fingerprint
                    raise ReferenceQueryError(code)
                if response.get("error") or "result" not in response:
                    raise QueryWorkerError(
                        "WISE query failed; inspect the private query log"
                    )
                self.models.add(model)
                self.feature_fingerprints[model] = fingerprint
                return response["result"]
        except ReferenceQueryError:
            raise
        except Exception as exc:
            self.close()
            if isinstance(exc, QueryWorkerError):
                raise
            raise QueryWorkerError("WISE query process is unavailable") from exc

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            if os.name != "nt":
                # Also clean descendants after an unexpected leader exit.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                # A descendant may ignore TERM or outlive the group leader;
                # kill the whole remaining group before closing pipe readers.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            process.wait(timeout=10)
            for thread in (self.reader, self.writer):
                if thread is not None:
                    thread.join(timeout=1)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
        if self.log is not None:
            self.log.close()
        self.log = self.reader = self.writer = self.responses = None
        self.models.clear()
        self.feature_fingerprints.clear()
