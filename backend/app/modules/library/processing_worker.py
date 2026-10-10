"""Bounded orchestration: one transfer/poll at a time, persisted remote identities."""

import logging
import shutil
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, case, func, or_, select, text

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.errors import AppError, ProviderError
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_processing import MediaProcessingRun
from app.models.storage import Blob
from app.modules.generation.media_engine import Canceled
from app.modules.library.processing import ACTIVE, enqueue_automatic
from app.modules.library.providers.mediacms import MediaCMSCancelled, MediaCMSError
from app.modules.library.providers.wise import WiseClient
from app.storage import cas

log = logging.getLogger(__name__)
QUEUE_LOCK = 726381912


def wise_client():
    return WiseClient(settings.library_wise_url, settings.library_wise_token)


def check_capacity(blob):
    if blob.size_bytes > settings.library_processing_max_transfer_bytes:
        raise ProviderError("素材超过单个处理任务的传输上限")
    folder = Path(settings.library_processing_temp_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(folder).free < blob.size_bytes + settings.library_processing_reserve_bytes:
        raise ProviderError("处理空间不足，请清理临时空间或扩容后重试")


def poll_wise(db, run, version, project_id, stopped):
    client = wise_client()
    remote = client.get_job(run.id)
    if run.operation == "delete":
        if remote is None:
            source_job_id = run.outputs.get("source_job_id")
            if source_job_id is None:
                # A local attempt may fail before reaching WISE. Anchor deletion
                # to an actual upstream source generation, not just the latest row.
                for candidate in run.outputs.get("source_job_ids", []):
                    if client.get_job(candidate) is not None:
                        source_job_id = candidate
                        run.outputs = {**run.outputs, "source_job_id": candidate}
                        db.commit()
                        break
            remote = client.delete(project_id, version.id, run.id, source_job_id=source_job_id)
        elif remote.get("status") == "failed" and run.outputs.get(
            "dispatch_retry", run.status == "queued"
        ):
            remote = client.retry(run.id)
        run.remote_id = str(run.id)
        return remote
    if run.cancel_requested:
        if remote is not None:
            remote = client.cancel(run.id)
            if remote.get("status") not in ("canceled", "ready", "failed"):
                return {"status": "waiting", "phase": "canceling"}
        return {"status": "canceled"}
    if remote is None:
        checked, cancel_seen = 0.0, False

        def interrupted():
            nonlocal checked, cancel_seen
            if stopped():
                return True
            if time.monotonic() - checked > 0.5:
                with SessionLocal() as state:
                    cancel_seen = bool(
                        state.scalar(
                            select(
                                MediaProcessingRun.cancel_requested,
                            ).where(MediaProcessingRun.id == run.id)
                        )
                    )
                checked = time.monotonic()
            return cancel_seen

        blob = db.get(Blob, run.source_hash)
        check_capacity(blob)
        with cas.local_file(
            blob, interrupted, temp_dir=settings.library_processing_temp_dir
        ) as path:
            if interrupted():
                return {"status": "waiting", "phase": "interrupted"}
            remote = client.submit(
                project_id,
                version.id,
                run.id,
                path,
                run.capabilities,
                canceled=interrupted,
                restore_after_job_id=run.outputs.get("restore_after_job_id"),
                media_type=version.media_type,
                mime_type=blob.mime,
            )
    elif remote.get("status") == "failed" and run.outputs.get(
        "dispatch_retry", run.status == "queued"
    ):
        remote = client.retry(run.id)
    run.remote_id = str(run.id)
    if remote.get("status") == "ready":
        capability_status = remote.get("capabilities", {})
        if not isinstance(capability_status, dict):
            raise ProviderError("WISE 返回无效的索引能力状态")
        clean = {}
        for cap in run.capabilities:
            item = capability_status.get(cap, {})
            count = item.get("vector_count", 0) if isinstance(item, dict) else 0
            state = item.get("state") if isinstance(item, dict) else None
            if state not in ("ready", "unavailable") or type(count) is not int or count < 0:
                raise ProviderError("WISE 未返回所选能力的索引完成凭据")
            if state == "ready" and count == 0:
                raise ProviderError("WISE 返回空索引，无法确认处理成功")
            clean[cap] = {"state": state, "vector_count": count}
        run.outputs = {**run.outputs, "capabilities": clean, "revision": remote.get("revision")}
    return remote


def process(run_id, stopped=lambda: False):
    with SessionLocal() as db:
        run = db.get(MediaProcessingRun, run_id)
        if run is None or run.status not in ACTIVE:
            return
        version = db.get(MediaVersion, run.version_id)
        media = db.get(LibraryMedia, version.media_id)
        if media.deleted_at and run.operation != "delete":
            run.cancel_requested = True
        was_queued = run.status == "queued"
        if was_queued or run.last_dispatched_at is None:
            # Persist before network I/O so a retry/restart cannot let one
            # project's older backlog monopolize the next provider slot.
            run.last_dispatched_at = datetime.now(UTC)
        if was_queued:
            run.outputs = {**run.outputs, "dispatch_retry": True}
        # Claim the provider slot durably BEFORE a request can be accepted. A
        # hard exit after remote acceptance must recover this ID before another
        # project's queued work can use the same provider. Retry intent survives
        # that exit independently from the scheduler's occupancy state.
        run.status = "processing"
        run.attempts += 1
        db.commit()
        try:
            if run.provider == "wise":
                remote = poll_wise(db, run, version, media.project_id, stopped)
            else:
                from app.modules.library.processing_mediacms import (
                    checkpoint,
                    client,
                    poll_cleanup,
                    poll_mediacms,
                )

                if run.operation == "delete":
                    complete = True
                    with client() as connection:
                        for identity in run.outputs["remote_ids"]:
                            if identity in run.outputs.get("removed_ids", []):
                                continue
                            if not poll_cleanup(connection, identity):
                                complete = False
                                break
                            checkpoint(
                                db,
                                run,
                                removed_ids=[
                                    *run.outputs.get("removed_ids", []),
                                    identity,
                                ],
                            )
                    remote = {
                        "status": "ready" if complete else "waiting",
                        "phase": "ready" if complete else "canceling",
                    }
                else:
                    remote = poll_mediacms(db, run, version, media.project_id, stopped)
            remote_status = remote.get("status")
            if remote_status not in (
                "queued",
                "processing",
                "waiting",
                "ready",
                "failed",
                "canceled",
            ):
                raise ProviderError("素材处理服务返回未知任务状态")
            run.outputs = {
                key: value for key, value in run.outputs.items() if key != "dispatch_retry"
            }
            # Serialize publication against cancel, trash and new submissions.
            db.scalar(select(LibraryMedia).where(LibraryMedia.id == media.id).with_for_update())
            local_remote_id = run.remote_id
            local_outputs = dict(run.outputs)
            db.refresh(run)
            run.remote_id = local_remote_id
            run.outputs = local_outputs
            if stopped() and remote_status not in ("canceled", "failed"):
                run.status = "waiting"
                run.progress = {"phase": "interrupted"}
            elif run.cancel_requested and remote_status not in ("canceled", "failed"):
                run.status = "waiting"
                run.progress = {"phase": "canceling"}
            else:
                run.status = (
                    "waiting" if remote_status in ("queued", "processing") else remote_status
                )
                phase = remote.get("phase", "")
                # Do not publish provider paths or arbitrary diagnostic text to browsers.
                run.progress = {
                    "phase": phase
                    if phase
                    in (
                        "queued",
                        "preparing",
                        "recovering",
                        "uploading",
                        "extracting",
                        "indexing",
                        "publishing",
                        "transcoding",
                        "transcribing",
                        "ready",
                        "canceling",
                        "interrupted",
                    )
                    else remote_status
                }
            if remote_status == "failed":
                raise ProviderError("上游素材处理失败，请查看服务日志后重试")
            if run.status == "ready" and run.provider == "mediacms" and run.operation == "process":
                from app.modules.library.processing_mediacms import publish

                publish(db, run, version, media.project_id)
            run.error = None
            run.next_poll_at = (
                (
                    datetime.now(UTC)
                    + timedelta(seconds=max(2, settings.library_processing_poll_seconds))
                )
                if run.status in ACTIVE
                else None
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            run = db.get(MediaProcessingRun, run_id)
            if isinstance(exc, (Canceled, MediaCMSCancelled)) or run.cancel_requested or stopped():
                # Reconcile before reporting cancellation; an upstream upload may exist.
                run.status = "waiting"
                run.next_poll_at = datetime.now(UTC) + timedelta(seconds=5)
                run.progress = {"phase": "canceling" if run.cancel_requested else "interrupted"}
                db.commit()
                return
            if (isinstance(exc, AppError) and exc.detail.get("retryable")) or (
                isinstance(exc, MediaCMSError) and exc.retryable
            ):
                # Timeout is not evidence of a stopped upstream task. Keep its slot
                # occupied and reconcile the deterministic ID before another upload.
                run.status = "waiting"
                run.error = "处理服务暂时无法连接，正在核对远端任务，可恢复后继续或请求停止"
                run.progress = {"phase": "reconciling"}
                run.next_poll_at = datetime.now(UTC) + timedelta(seconds=30)
                db.commit()
                return
            run.status = "failed"
            run.error = (
                exc.message if isinstance(exc, AppError) else "素材处理失败，请检查服务后重试"
            )
            run.next_poll_at = None
            db.commit()
            log.warning(
                "Media provider run %s failed (%s; initial=%s)",
                run_id,
                type(exc).__name__,
                was_queued,
            )


def run_once(stopped=lambda: False):
    with engine.connect() as conn:
        if not conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": QUEUE_LOCK}):
            return False
        try:
            with SessionLocal() as db:
                queued = enqueue_automatic(db)
                busy = list(
                    db.scalars(
                        select(MediaProcessingRun.provider)
                        .where(
                            MediaProcessingRun.status.in_(("waiting", "processing")),
                        )
                        .distinct()
                    )
                )
                history = (
                    select(
                        LibraryMedia.project_id,
                        MediaProcessingRun.provider,
                        func.max(MediaProcessingRun.last_dispatched_at).label("last_dispatch"),
                    )
                    .select_from(MediaProcessingRun)
                    .join(MediaVersion, MediaVersion.id == MediaProcessingRun.version_id)
                    .join(LibraryMedia, LibraryMedia.id == MediaVersion.media_id)
                    .group_by(LibraryMedia.project_id, MediaProcessingRun.provider)
                    .subquery()
                )
                run_id = db.scalar(
                    select(MediaProcessingRun.id)
                    .join(MediaVersion, MediaVersion.id == MediaProcessingRun.version_id)
                    .join(LibraryMedia, LibraryMedia.id == MediaVersion.media_id)
                    .outerjoin(
                        history,
                        and_(
                            history.c.project_id == LibraryMedia.project_id,
                            history.c.provider == MediaProcessingRun.provider,
                        ),
                    )
                    .where(
                        MediaProcessingRun.status.in_(ACTIVE),
                        or_(
                            MediaProcessingRun.status != "queued",
                            MediaProcessingRun.provider.not_in(busy),
                            MediaProcessingRun.cancel_requested.is_(True),
                        ),
                        or_(
                            MediaProcessingRun.next_poll_at.is_(None),
                            MediaProcessingRun.next_poll_at <= datetime.now(UTC),
                        ),
                    )
                    .order_by(
                        # Cancellation and reconciliation release scarce remote
                        # slots first. New work then rotates across projects;
                        # within a project, retain FIFO and a stable tie-break.
                        case((MediaProcessingRun.cancel_requested.is_(True), 0), else_=1),
                        case((MediaProcessingRun.status != "queued", 0), else_=1),
                        history.c.last_dispatch.asc().nulls_first(),
                        MediaProcessingRun.updated_at,
                        MediaProcessingRun.created_at,
                        MediaProcessingRun.id,
                    )
                    .limit(1)
                )
            if run_id and not stopped():
                process(run_id, stopped)
            return run_id is not None or queued
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": QUEUE_LOCK})
            conn.commit()


def start():
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                worked = run_once(stop.is_set)
            except Exception:
                worked = False
                log.warning(
                    "Media processing queue unavailable; check schema and service configuration"
                )
            if not worked:
                stop.wait(2)

    thread = threading.Thread(target=loop, name="media-providers", daemon=True)
    thread.start()

    def shutdown():
        stop.set()
        thread.join(timeout=5)

    return shutdown
