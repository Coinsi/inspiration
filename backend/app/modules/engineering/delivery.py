"""At-least-once delivery of already committed, local engineering render jobs.

Publishing is not worker acknowledgement. Pending jobs are redelivered after a
bounded delay; jobs.execute claims under its job row lock before doing any work.
No external provider operation is automatically replayed by this service.
"""

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


def eligible(job):
    snapshot = job.input_snapshot or {}
    return (
        job.provider == "local"
        and snapshot.get("engineering_change_id")
        and snapshot.get("operation") in {"render", "engineering_image", "engineering_render"}
    )


def enqueue(db, job):
    """Never commit here: candidate link, job and delivery must commit together."""
    if not eligible(job):
        raise ValueError("Only local engineering previews use this delivery contract")
    row = db.get(EngineeringDelivery, job.id)
    if row is None:
        row = EngineeringDelivery(job_id=job.id, next_attempt_at=datetime.now(UTC))
        db.add(row)
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
        row.state, row.last_error = "rejected", "Job no longer satisfies local engineering contract"
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
    row.lease_token = uuid.uuid4()
    row.lease_expires_at = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
    row.state = "acknowledged"
    return row.lease_token


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
                log.exception("Engineering heartbeat failed; lease will expire unless renewed")

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
