"""Trusted-backend-only HTTP API. The public app owns user permissions."""

import hashlib
import hmac
import json
import logging
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from wise_bridge import MODELS, WISE_COMMIT
from wise_bridge.config import Settings
from wise_bridge.optional_models import (
    OPTIONAL,
    OptionalModelUnavailable,
    inspect_optional,
    require_optional,
)
from wise_bridge.query_errors import ReferenceQueryError
from wise_bridge.runner import EngineFailure, Worker
from wise_bridge.store import Conflict, Store

Capability = Literal["visual", "audio", "face", "object"]
logger = logging.getLogger(__name__)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    modality: Capability = "visual"
    source_ids: list[uuid.UUID] | None = Field(default=None, max_length=10000)
    limit: int = Field(default=20, ge=1, le=100)


class BBox(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


def job_view(job, settings):
    result = job.get("result") or {}
    counts = result.get("counts", {}).get(job["source_id"], {})
    requested = job["payload"].get("capabilities", [])
    capabilities = {}
    for cap in MODELS:
        state = (
            "disabled"
            if cap not in settings.enabled_capabilities or cap not in requested
            else "pending"
        )
        if job["state"] == "ready" and cap in requested:
            state = "ready" if counts.get(cap, 0) else "unavailable"
        capabilities[cap] = {"state": state, "vector_count": counts.get(cap, 0)}
    return {
        "job_id": job["id"],
        "project_id": job["project_id"],
        "source_id": job["source_id"],
        "status": job["state"],
        "phase": job["phase"],
        "error": job["error"],
        "capabilities": capabilities,
        "revision": result.get("revision"),
        "attempts": job["attempts"],
        "generation": job["generation"],
    }


class BodyLimit:
    """Bound multipart parser spooling too, including chunked HTTP bodies."""

    def __init__(self, app, limit, data_dir, min_free_bytes):
        self.app, self.limit = app, limit
        self.data_dir, self.min_free_bytes = data_dir, min_free_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            too_large = int(headers.get(b"content-length", b"0")) > self.limit
        except ValueError:
            too_large = True
        if too_large:
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            return await send(
                {
                    "type": "http.response.body",
                    "body": b'{"detail":"Upload exceeds service limit"}',
                }
            )
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > self.limit:
                raise HTTPException(413, "Upload exceeds service limit")
            if shutil.disk_usage(self.data_dir).free < self.min_free_bytes + len(
                message.get("body", b"")
            ):
                raise HTTPException(507, "Processing storage capacity is exhausted")
            return message

        await self.app(scope, bounded_receive, send)


def create_app(settings=None, runner=None):
    settings = settings or Settings()
    settings.validate()
    store = Store(settings.data_dir)
    # Starlette parses multipart before the endpoint starts. Keep its spooled
    # uploads on the guarded data volume, not an unbounded container /tmp.
    spool_dir = settings.data_dir / "upload-spool"
    spool_dir.mkdir(exist_ok=True)
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(spool_dir)
    worker = Worker(settings, store, runner)

    @asynccontextmanager
    async def lifespan(app):
        try:
            if settings.worker_enabled:
                worker.start()
            yield
        finally:
            worker.stop()
            if tempfile.tempdir == str(spool_dir):
                tempfile.tempdir = previous_tempdir

    def authenticate(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + settings.token):
            raise HTTPException(401, "Service authentication required")

    app = FastAPI(
        title="Inspiration WISE bridge",
        version="0.1.0",
        lifespan=lifespan,
        dependencies=[Depends(authenticate)],
    )
    app.add_middleware(
        BodyLimit,
        limit=settings.max_file_bytes + 1024**2,
        data_dir=settings.data_dir,
        min_free_bytes=settings.min_free_bytes,
    )
    app.state.store, app.state.worker = store, worker

    def get_job(job_id):
        job = store.job(str(job_id))
        if not job:
            raise HTTPException(404, "Task not found")
        return job

    def validate_capability(cap):
        if cap not in settings.enabled_capabilities:
            raise HTTPException(422, "Requested capability is disabled in this service")
        try:
            require_optional([cap], settings)
        except OptionalModelUnavailable as exc:
            raise HTTPException(422, {"code": exc.reason, "capability": cap}) from None

    @app.get("/health")
    def health():
        return {
            "service": "inspiration-wise-bridge",
            "version": "0.1.0",
            "upstream_commit": WISE_COMMIT,
            "worker_alive": bool(worker.thread and worker.thread.is_alive()),
            "upstream_installation_verified": worker.verified,
            "models_verified": False,
            "capabilities": {
                cap: inspect_optional(cap, settings)
                if cap in OPTIONAL
                else {
                    "configured": cap in settings.enabled_capabilities,
                    "model_id": model,
                    "availability": "check_source_job",
                }
                for cap, model in MODELS.items()
            },
            "last_error": worker.probe_error,
            "heavy_process_concurrency": 1,
        }

    @app.post("/v1/projects/{project_id}/sources/{source_id}", status_code=202)
    async def submit(
        project_id: uuid.UUID,
        source_id: uuid.UUID,
        file: Annotated[UploadFile, File()],
        job_id: Annotated[uuid.UUID, Form()],
        media_type: Annotated[Literal["video", "image", "audio"], Form()],
        capabilities: Annotated[str, Form()] = '["visual","audio"]',
        restore_after_job_id: Annotated[uuid.UUID | None, Form()] = None,
    ):
        try:
            caps = json.loads(capabilities)
            if (
                not isinstance(caps, list)
                or not caps
                or any(not isinstance(cap, str) or cap not in MODELS for cap in caps)
            ):
                raise ValueError()
            caps = sorted(set(caps))
        except (ValueError, TypeError):
            raise HTTPException(
                422, "Capabilities must be a nonempty JSON array"
            ) from None
        for cap in caps:
            validate_capability(cap)
        if media_type == "audio" and any(cap != "audio" for cap in caps):
            raise HTTPException(422, "Audio sources support only audio indexing")
        if media_type == "image" and "audio" in caps:
            raise HTTPException(422, "Image sources do not support audio indexing")
        suffix = Path(file.filename or "").suffix.lower()
        allowed_suffixes = {
            "video": {".mp4", ".webm", ".mov", ".mkv"},
            "image": {".jpg", ".jpeg", ".png", ".webp"},
            "audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg"},
        }
        if suffix not in allowed_suffixes[media_type]:
            raise HTTPException(422, "Unsupported analysis media extension")
        source_dir = settings.data_dir / "sources" / str(project_id)
        source_dir.mkdir(parents=True, exist_ok=True)
        destination = source_dir / (str(source_id) + suffix)
        temporary = source_dir / (str(uuid.uuid4()) + ".upload")
        digest = hashlib.sha256()
        size = 0
        created = False
        accepted = False
        try:
            used = await run_in_threadpool(
                lambda: sum(
                    p.stat().st_size
                    for p in settings.data_dir.rglob("*")
                    if p.is_file()
                )
            )
            with temporary.open("xb") as target:
                while chunk := await file.read(1024**2):
                    size += len(chunk)
                    if size > settings.max_file_bytes:
                        raise HTTPException(413, "Analysis media exceeds service limit")
                    if (
                        used + size > settings.max_storage_bytes
                        or shutil.disk_usage(settings.data_dir).free
                        < settings.min_free_bytes
                    ):
                        raise HTTPException(
                            507, "Processing storage capacity is exhausted"
                        )
                    digest.update(chunk)
                    target.write(chunk)
            if not size:
                raise HTTPException(422, "Empty analysis media")
            payload = {
                "file": str(destination),
                "sha256": digest.hexdigest(),
                "size": size,
                "media_type": media_type,
                "capabilities": caps,
            }
            existing = store.job(str(job_id))
            if existing:
                if (
                    existing["payload"] != payload
                    or existing["project_id"] != str(project_id)
                    or existing["source_id"] != str(source_id)
                    or existing["kind"] != "upsert"
                    or existing["restore_after_job_id"]
                    != (str(restore_after_job_id) if restore_after_job_id else None)
                ):
                    raise HTTPException(409, "Job id has a different payload")
                return job_view(existing, settings)

            def install_file():
                nonlocal created
                # Store's lifecycle transaction serializes adoption with cleanup.
                # A restored file can never be unlinked between link and enqueue.
                if destination.exists():
                    if _file_hash(destination) != payload["sha256"]:
                        raise Conflict(
                            "Source version already exists with different content"
                        )
                else:
                    destination.hardlink_to(temporary)
                    created = True

            try:
                job = await run_in_threadpool(
                    store.enqueue,
                    str(job_id),
                    str(project_id),
                    str(source_id),
                    "upsert",
                    payload,
                    settings.max_pending_jobs,
                    restore_after_job_id=str(restore_after_job_id)
                    if restore_after_job_id
                    else None,
                    install_file=install_file,
                )
                accepted = True
            except Conflict as exc:
                raise HTTPException(409, str(exc)) from None
            worker.wake.set()
            return job_view(job, settings)
        finally:
            temporary.unlink(missing_ok=True)
            await file.close()
            # Accepted sources are owned by the durable database; failed upload
            # files are only removed if no source/job has adopted them.
            if created and not accepted:
                with store.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    adopted = conn.execute(
                        "SELECT payload,deleted FROM sources WHERE project_id=? AND id=?",
                        (str(project_id), str(source_id)),
                    ).fetchone()
                    if (
                        not adopted
                        or adopted["deleted"]
                        or json.loads(adopted["payload"]).get("sha256")
                        != payload["sha256"]
                    ):
                        destination.unlink(missing_ok=True)

    @app.get("/v1/jobs/{job_id}")
    def task(job_id: uuid.UUID):
        return job_view(get_job(job_id), settings)

    @app.post("/v1/jobs/{job_id}/cancel")
    def cancel(job_id: uuid.UUID):
        job = get_job(job_id)
        if job["kind"] == "delete":
            raise HTTPException(409, "Deletion cleanup cannot be canceled")
        return job_view(store.cancel(str(job_id)), settings)

    @app.post("/v1/jobs/{job_id}/retry", status_code=202)
    def retry(job_id: uuid.UUID):
        get_job(job_id)
        try:
            result = store.retry(str(job_id))
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        worker.wake.set()
        return job_view(result, settings)

    @app.delete("/v1/projects/{project_id}/sources/{source_id}", status_code=202)
    def delete(
        project_id: uuid.UUID,
        source_id: uuid.UUID,
        job_id: Annotated[uuid.UUID, Query()],
        source_job_id: Annotated[uuid.UUID | None, Query()] = None,
    ):
        try:
            job = store.enqueue(
                str(job_id),
                str(project_id),
                str(source_id),
                "delete",
                {},
                settings.max_pending_jobs,
                source_job_id=str(source_job_id) if source_job_id else None,
            )
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        worker.wake.set()
        return job_view(job, settings)

    async def search_response(project_id, payload):
        validate_capability(payload["modality"])
        try:
            return await run_in_threadpool(worker.search, str(project_id), payload)
        except ReferenceQueryError as exc:
            raise HTTPException(422, {"code": exc.code}) from None
        except EngineFailure as exc:
            raise HTTPException(503, str(exc)) from None
        except Exception:
            logger.exception("WISE search failed for project %s", project_id)
            raise HTTPException(
                503, "WISE search failed; inspect the private service log"
            ) from None

    @app.post("/v1/projects/{project_id}/search")
    async def text_search(project_id: uuid.UUID, request: SearchRequest):
        if request.modality == "face":
            raise HTTPException(422, "Face search requires a reference image")
        return await search_response(project_id, request.model_dump(mode="json"))

    @app.post("/v1/projects/{project_id}/search/media")
    async def media_search(
        project_id: uuid.UUID,
        file: Annotated[UploadFile, File()],
        modality: Annotated[Capability, Form()] = "visual",
        source_ids: Annotated[str | None, Form()] = None,
        limit: Annotated[int, Form(ge=1, le=100)] = 20,
        bbox: Annotated[str | None, Form()] = None,
    ):
        validate_capability(modality)
        try:
            ids = (
                None
                if source_ids is None
                else [str(uuid.UUID(value)) for value in json.loads(source_ids)]
            )
            if ids is not None and len(ids) > 10000:
                raise ValueError()
            box = BBox.model_validate_json(bbox).model_dump() if bbox else None
            if box and (box["x"] + box["w"] > 1 or box["y"] + box["h"] > 1):
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(422, "Invalid source_ids or image region") from None
        runtime = settings.data_dir / "runtime"
        runtime.mkdir(exist_ok=True)
        path = runtime / (str(uuid.uuid4()) + ".query")
        try:
            size = 0
            with path.open("xb") as output:
                while chunk := await file.read(1024**2):
                    size += len(chunk)
                    if size > settings.max_query_bytes:
                        raise HTTPException(413, "Reference media exceeds query limit")
                    output.write(chunk)
            if not size:
                raise HTTPException(422, "Empty reference media")
            return await search_response(
                project_id,
                {
                    "query_file": str(path),
                    "modality": modality,
                    "source_ids": ids,
                    "limit": limit,
                    "bbox": box,
                },
            )
        finally:
            path.unlink(missing_ok=True)
            await file.close()

    return app


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024**2):
            digest.update(chunk)
    return digest.hexdigest()
