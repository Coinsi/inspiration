"""Durable delivery with distinct local replay and remote receipt policies.

Publishing is not worker acknowledgement. Pending jobs are redelivered after a
bounded delay; jobs.execute claims under its job row lock before doing any work.
Remote production jobs may be redelivered only before their conditional claim.
After a worker loss they require explicit receipt recovery, never a paid replay.
"""

import copy
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.engineering import EngineeringDelivery
from app.models.generation import GenerationJob

log = logging.getLogger(__name__)
LEASE_SECONDS = 60
LEASE_CONTEXT = "generation_delivery_leases"


class LeaseLost(RuntimeError):
    """A stale worker must not write receipts, publish outputs, or cancel upstream."""


def policy(job):
    snapshot = job.input_snapshot or {}
    origin = snapshot.get("harness_origin")
    if (
        job.provider != "local"
        and isinstance(origin, dict)
        and origin.get("tool", "media.generate") == "media.generate"
        and all(origin.get(key) for key in ("run_id", "task_id", "request_key"))
        and snapshot.get("operation", "generate") == "generate"
    ):
        return "remote_receipt"
    return "local_replay" if _local_eligible(job) else None


def _local_eligible(job):
    snapshot = job.input_snapshot or {}
    engineering = bool(snapshot.get("engineering_change_id")) and snapshot.get("operation") in {
        "render",
        "engineering_image",
        "engineering_render",
    }
    process = (snapshot.get("harness_origin") or {}).get(
        "tool"
    ) == "media.process" and snapshot.get("operation") in {
        "refine",
        "video_frames",
        "video_audio",
        "video_trim",
    }
    return job.provider == "local" and (engineering or process)


def eligible(job):
    return policy(job) is not None


def enqueue(db, job):
    """Never commit here: candidate link, job and delivery must commit together."""
    if not eligible(job):
        raise ValueError("Job does not satisfy a durable production delivery contract")
    row = db.get(EngineeringDelivery, job.id)
    if row is None:
        row = EngineeringDelivery(job_id=job.id, next_attempt_at=datetime.now(UTC))
        db.add(row)
    elif job.status == "pending" and row.state != "pending":
        # Only the explicit same-job receipt recovery may rearm a remote claim.
        # The caller holds the job lock and has validated the frozen recovery guard.
        if policy(job) == "remote_receipt":
            state = job.routing_state or {}
            if not state.get("harness_receipt_recovery") or not state.get("resume"):
                raise ValueError("Remote execution cannot be requeued without receipt recovery")
        if row.lease_expires_at and row.lease_expires_at > datetime.now(UTC):
            raise LeaseLost("The previous worker still owns this job")
        row.state, row.next_attempt_at, row.last_error = "pending", datetime.now(UTC), None
        row.lease_token, row.lease_expires_at = None, None
    db.flush()
    return row


def publish(job):
    if settings.generation_executor == "local":
        from app.modules.generation import jobs

        jobs.pool.submit(jobs._local, job.id)
    else:
        from app.tasks.celery_app import celery_app
        from app.tasks.generation_tasks import run_generation_job

        with celery_app.connection_for_write(
            connect_timeout=2,
            transport_options={
                "socket_timeout": 2,
                "socket_connect_timeout": 2,
            },
        ) as connection:
            run_generation_job.apply_async(args=[str(job.id)], retry=False, connection=connection)


def deliver(db, row, now=None):
    now = now or datetime.now(UTC)
    job = db.get(GenerationJob, row.job_id)
    if job is None or job.status != "pending":
        row.state, row.last_error = "acknowledged", None
        return
    if not eligible(job):
        row.state, row.last_error = "rejected", "Job no longer satisfies delivery contract"
        return
    row.attempts += 1
    try:
        publish(job)
        row.last_error = None
        delay = 30  # Broker accepted does not prove a worker claimed it.
    except Exception as exc:
        row.last_error = type(exc).__name__  # Never persist credentials from broker exception text.
        delay = min(300, 5 * 2 ** min(row.attempts, 6))
    row.next_attempt_at = now + timedelta(seconds=delay)


def claim(db, job):
    """Caller already holds the job lock. Tokens fence late workers after recovery."""
    row = db.scalar(
        select(EngineeringDelivery).where(EngineeringDelivery.job_id == job.id).with_for_update()
    )
    if row is None:
        row = enqueue(db, job)
    if row.lease_expires_at and row.lease_expires_at > datetime.now(UTC):
        raise LeaseLost("The previous worker still owns this job")
    row.lease_token = uuid.uuid4()
    row.lease_expires_at = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
    row.state = "acknowledged"
    return row.lease_token


def fence(db, job_id):
    """Enforce the executing session's token at every provider receipt mutation.

    Callers take the job row lock before this check; recovery uses the same lock
    order. Standalone legacy adapter calls have no worker token and are unchanged.
    """
    token = db.info.get(LEASE_CONTEXT, {}).get(job_id)
    if token is not None and not owned(db, job_id, token):
        raise LeaseLost("Generation worker lease expired; receipt belongs to a newer owner")


def remote_phase(db, job_id, phase, external_job_id=None):
    """Also track bare adapters, whose submit may not implement durable phases."""
    job = db.scalar(
        select(GenerationJob)
        .where(GenerationJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    fence(db, job_id)
    if policy(job) != "remote_receipt":
        return
    if phase == "dispatching" and job.status == "canceled":
        from app.modules.generation.media_engine import Canceled

        raise Canceled()
    state = copy.deepcopy(job.routing_state or {})
    state["phase"] = phase
    if external_job_id:
        if job.external_job_id and job.external_job_id != external_job_id:
            raise ValueError("A receipt cannot replace the frozen upstream job identifier")
        state["external_job_id"] = external_job_id
        job.external_job_id = external_job_id
    job.routing_state = state


def release(job_id, token, sessions):
    """A completed worker need not wait for expiry before explicit receipt recovery."""
    if token is None:
        return
    with sessions() as db:
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
        row = db.scalar(
            select(EngineeringDelivery)
            .where(EngineeringDelivery.job_id == job_id)
            .with_for_update()
        )
        if (
            job
            and row
            and job.status in {"succeeded", "failed", "canceled"}
            and row.lease_token == token
        ):
            row.lease_token, row.lease_expires_at = None, None
            db.commit()


def _remote_lost(job, row):
    from app.modules.generation.jobs import event
    from app.modules.harness.recovery_tools import resume_problem

    state = copy.deepcopy(job.routing_state or {})
    known = bool(
        job.external_job_id
        and state.get("external_job_id") == job.external_job_id
        and state.get("phase") in {"accepted", "generated"}
    )
    # A claim alone does not prove that submission never reached the provider.
    # Leave a durable uncertain marker, including for adapters without phases.
    if not known and state.get("phase") != "upstream_failed":
        state["phase"] = "submission_unknown"
    job.status, job.routing_state = "failed", state
    recovery = "resume_receipt" if known and resume_problem(job) is None else "uncertain"
    state["harness_delivery"] = {
        "policy": "remote_receipt",
        "recovery": recovery,
        "worker_lost_at": datetime.now(UTC).isoformat(),
        "automatic_resubmit": False,
    }
    job.routing_state = state
    job.error = (
        "生成工作进程失联，原上游任务编号已保留；可继续接收同一任务，不会重新生成"
        if recovery == "resume_receipt"
        else "生成工作进程失联，无法安全确认或接收上游结果；"
        "费用预留保留，请核对原任务，禁止自动重发"
    )
    event(job, "failed", job.error)
    row.state = "acknowledged"  # Terminal delivery; never picked by pump.
    row.last_error = "RemoteWorkerLost"


def owned(db, job_id, token):
    if token is None:
        return True
    row = db.execute(
        select(EngineeringDelivery.lease_token, EngineeringDelivery.lease_expires_at).where(
            EngineeringDelivery.job_id == job_id
        )
    ).first()
    return bool(row and row[0] == token and row[1] and row[1] > datetime.now(UTC))


def heartbeat(job_id, token, sessions):
    stop = threading.Event()

    def run():
        while not stop.wait(10):
            try:
                with sessions() as db:
                    row = db.scalar(
                        select(EngineeringDelivery)
                        .where(EngineeringDelivery.job_id == job_id)
                        .with_for_update()
                    )
                    if (
                        not row
                        or row.lease_token != token
                        or not row.lease_expires_at
                        or row.lease_expires_at <= datetime.now(UTC)
                    ):
                        return  # Expired ownership is never resurrected by a delayed heartbeat.
                    row.lease_expires_at = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
                    db.commit()
            except Exception:
                log.exception("Production heartbeat failed; lease will expire unless renewed")

    thread = threading.Thread(target=run, name="engineering-lease", daemon=True)
    thread.start()

    def shutdown():
        stop.set()
        thread.join(timeout=2)

    return shutdown


def recover_expired(limit=20):
    from app.modules.generation.jobs import event

    recovered = 0
    for _ in range(limit):
        with SessionLocal() as db:
            job = db.scalar(
                select(GenerationJob)
                .join(EngineeringDelivery, EngineeringDelivery.job_id == GenerationJob.id)
                .where(
                    GenerationJob.status.in_(("submitted", "running")),
                    EngineeringDelivery.lease_expires_at <= datetime.now(UTC),
                )
                .with_for_update(of=GenerationJob, skip_locked=True)
                .limit(1)
            )
            if not job:
                break
            row = db.scalar(
                select(EngineeringDelivery)
                .where(EngineeringDelivery.job_id == job.id)
                .with_for_update()
            )
            if not row or not row.lease_expires_at or row.lease_expires_at > datetime.now(UTC):
                db.rollback()
                continue
            if not eligible(job):
                row.lease_expires_at = None
                row.state = "rejected"
            else:
                row.lease_token = None
                row.lease_expires_at = None
                if policy(job) == "remote_receipt":
                    _remote_lost(job, row)
                else:
                    row.state = "pending"
                    row.next_attempt_at = datetime.now(UTC)
                    job.error = None
                    event(job, "pending", "工程工作进程租约过期，已从固定输入重新排队")
                recovered += 1
            db.commit()
    return recovered


def pump(limit=20):
    handled = 0
    for _ in range(limit):
        with SessionLocal() as db:
            row = db.scalar(
                select(EngineeringDelivery)
                .where(
                    EngineeringDelivery.state == "pending",
                    EngineeringDelivery.next_attempt_at <= datetime.now(UTC),
                )
                .order_by(EngineeringDelivery.next_attempt_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                break
            deliver(db, row)
            db.commit()
            handled += 1
    return handled


def start():
    stop = threading.Event()

    def run():
        while not stop.is_set():
            try:
                recover_expired(limit=10)
                pump(limit=10)
            except Exception:
                log.exception(
                    "Engineering delivery sweep failed; committed jobs remain recoverable"
                )
            stop.wait(5)

    thread = threading.Thread(target=run, name="engineering-delivery", daemon=True)
    thread.start()

    def shutdown():
        stop.set()
        thread.join(timeout=5)

    shutdown.is_alive = thread.is_alive
    return shutdown
