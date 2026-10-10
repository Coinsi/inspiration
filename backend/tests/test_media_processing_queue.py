"""Automatic scan and provider slots against isolated PostgreSQL schemas."""

# ruff: noqa: F811
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import Settings, settings
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_processing import MediaProcessingRun
from app.modules.library import processing, processing_worker
from tests.test_library import library, media_studio  # noqa: F401
from tests.test_media_processing import setup


@pytest.fixture(autouse=True)
def no_background_scan(monkeypatch):
    monkeypatch.setattr(settings, "library_processing_enabled", False)
    monkeypatch.setattr(settings, "library_processing_auto", False)


def configure_auto(monkeypatch):
    monkeypatch.setattr(settings, "library_processing_enabled", True)
    monkeypatch.setattr(settings, "library_processing_auto", True)
    monkeypatch.setattr(settings, "library_wise_url", "http://wise.test")
    monkeypatch.setattr(settings, "library_wise_token", "wise-test")
    monkeypatch.setattr(settings, "library_wise_capabilities", ["visual", "audio"])
    monkeypatch.setattr(settings, "library_mediacms_url", "http://media.test")
    monkeypatch.setattr(settings, "library_mediacms_token", "media-test")
    monkeypatch.setattr(settings, "library_mediacms_transcription_enabled", False)


def configure_worker(monkeypatch, sessions):
    monkeypatch.setattr(processing_worker, "SessionLocal", sessions)
    monkeypatch.setattr(processing_worker, "engine", sessions.kw["bind"])
    # Do not contend with the application's running worker or other tests.
    monkeypatch.setattr(processing_worker, "QUEUE_LOCK", uuid.uuid4().int % (2**62))


def copy_version(db, source, ordinal):
    clone = MediaVersion(
        media_id=source.media_id,
        ordinal=ordinal,
        filename=source.filename,
        fingerprint=source.fingerprint,
        size_bytes=source.size_bytes,
        uploaded_bytes=source.uploaded_bytes,
        status="ready",
        original_hash=source.original_hash,
        proxy_hash=source.proxy_hash,
        duration_ms=source.duration_ms,
        width=source.width,
        height=source.height,
    )
    db.add(clone)
    db.flush()
    return clone


def new_run(db, version, user, *, provider="wise", status="queued", next_poll_at=None):
    row = MediaProcessingRun(
        version_id=version.id,
        provider=provider,
        source_hash=version.original_hash,
        created_by=user,
        capabilities=["visual"] if provider == "wise" else ["preview"],
        status=status,
        next_poll_at=next_poll_at,
    )
    db.add(row)
    db.flush()
    return row


def test_automatic_defaults_off_and_does_not_touch_database(monkeypatch):
    assert Settings.model_fields["library_processing_enabled"].default is False
    assert Settings.model_fields["library_processing_auto"].default is False

    class NoDatabase:
        def scalar(self, _):
            raise AssertionError("Disabled automatic processing should not query storage")

    monkeypatch.setattr(settings, "library_processing_enabled", True)
    monkeypatch.setattr(settings, "library_processing_auto", False)
    assert processing.enqueue_automatic(NoDatabase()) is False
    monkeypatch.setattr(settings, "library_processing_enabled", False)
    monkeypatch.setattr(settings, "library_processing_auto", True)
    assert processing.enqueue_automatic(NoDatabase()) is False


def test_automatic_requires_config_and_selects_expected_capabilities(library, monkeypatch):
    _, sessions, ids, _, info = setup(library, monkeypatch)
    configure_auto(monkeypatch)
    monkeypatch.setattr(settings, "library_wise_token", None)
    monkeypatch.setattr(settings, "library_mediacms_url", None)
    with sessions() as db:
        assert processing.enqueue_automatic(db) is False
        assert db.scalar(select(func.count()).select_from(MediaProcessingRun)) == 0
        monkeypatch.setattr(settings, "library_wise_token", "wise-test")
        assert processing.enqueue_automatic(db) is True
        assert processing.enqueue_automatic(db) is False  # first provider is not duplicated
        monkeypatch.setattr(settings, "library_mediacms_url", "http://media.test")
        assert processing.enqueue_automatic(db) is True
        assert processing.enqueue_automatic(db) is False
        runs = {row.provider: row for row in db.scalars(select(MediaProcessingRun))}
        assert set(runs) == {"wise", "mediacms"}
        assert runs["wise"].capabilities == ["audio", "visual"]
        assert runs["wise"].source_hash == info["proxy_hash"]
        assert runs["mediacms"].capabilities == ["preview"]
        assert runs["mediacms"].source_hash == info["original_hash"]
        assert all(
            row.created_by == ids["user"] and row.operation == "process" and row.status == "queued"
            for row in runs.values()
        )


def test_automatic_does_not_restart_failed_or_canceled_work(library, monkeypatch):
    _, sessions, ids, _, info = setup(library, monkeypatch)
    configure_auto(monkeypatch)
    with sessions() as db:
        version = db.get(MediaVersion, uuid.UUID(info["id"]))
        failed = new_run(db, version, ids["user"], provider="wise", status="failed")
        canceled = new_run(db, version, ids["user"], provider="mediacms", status="canceled")
        db.commit()
        assert processing.enqueue_automatic(db) is False
        assert db.scalar(select(func.count()).select_from(MediaProcessingRun)) == 2
        db.refresh(failed)
        db.refresh(canceled)
        assert failed.status == "failed" and canceled.status == "canceled"


def test_queue_at_capacity_still_processes_existing_work(library, monkeypatch):
    _, sessions, ids, _, info = setup(library, monkeypatch)
    configure_auto(monkeypatch)
    configure_worker(monkeypatch, sessions)
    with sessions() as db:
        source = db.get(MediaVersion, uuid.UUID(info["id"]))
        new_run(db, source, ids["user"])
        for ordinal in range(2, 101):
            new_run(db, copy_version(db, source, ordinal), ids["user"])
        db.commit()
        assert db.scalar(select(func.count()).select_from(MediaProcessingRun)) == 100
        assert processing.enqueue_automatic(db) is False
    processed = []
    monkeypatch.setattr(
        processing_worker, "process", lambda run_id, stopped: processed.append(run_id)
    )
    assert processing_worker.run_once() is True
    assert len(processed) == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(MediaProcessingRun)) == 100


def test_waiting_provider_blocks_next_upload_but_other_provider_progresses(library, monkeypatch):
    _, sessions, ids, _, info = setup(library, monkeypatch)
    configure_worker(monkeypatch, sessions)
    future = datetime.now(UTC) + timedelta(hours=1)
    with sessions() as db:
        source = db.get(MediaVersion, uuid.UUID(info["id"]))
        queued = new_run(db, source, ids["user"])
        waiting = new_run(
            db, copy_version(db, source, 2), ids["user"], status="waiting", next_poll_at=future
        )
        other = new_run(db, source, ids["user"], provider="mediacms")
        db.commit()
        queued_id, waiting_id, other_id = queued.id, waiting.id, other.id
    processed = []

    def process(run_id, stopped):
        processed.append(run_id)
        with sessions() as db:
            run = db.get(MediaProcessingRun, run_id)
            if run_id == waiting_id:
                run.status, run.next_poll_at = "ready", None
            else:
                run.status, run.next_poll_at = "waiting", future
            db.commit()

    monkeypatch.setattr(processing_worker, "process", process)
    assert processing_worker.run_once() is True
    assert processed == [other_id]
    assert processing_worker.run_once() is False
    assert processed == [other_id]
    with sessions() as db:
        db.get(MediaProcessingRun, waiting_id).next_poll_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
        db.commit()
    assert processing_worker.run_once() is True
    assert processed == [other_id, waiting_id]  # reconcile the existing job first
    assert processing_worker.run_once() is True
    assert processed == [other_id, waiting_id, queued_id]


def test_database_advisory_lock_prevents_concurrent_dispatch(library, monkeypatch):
    _, sessions, ids, _, info = setup(library, monkeypatch)
    configure_worker(monkeypatch, sessions)
    with sessions() as db:
        source = db.get(MediaVersion, uuid.UUID(info["id"]))
        first = new_run(db, source, ids["user"])
        # PostgreSQL now() is fixed within one transaction; make the expected
        # oldest entry explicit rather than relying on physical insertion order.
        first.created_at = first.updated_at = datetime.now(UTC) - timedelta(seconds=1)
        new_run(db, copy_version(db, source, 2), ids["user"])
        db.commit()
        first_id = first.id
    started, release = threading.Event(), threading.Event()
    processed = []

    def slow_dispatch(run_id, stopped):
        processed.append(run_id)
        started.set()
        assert release.wait(timeout=10)
        with sessions() as db:
            run = db.get(MediaProcessingRun, run_id)
            run.status = "waiting"
            run.next_poll_at = datetime.now(UTC) + timedelta(hours=1)
            db.commit()

    monkeypatch.setattr(processing_worker, "process", slow_dispatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(processing_worker.run_once)
        try:
            assert started.wait(timeout=10)
            assert pool.submit(processing_worker.run_once).result(timeout=10) is False
            assert processed == [first_id]
        finally:
            release.set()
        assert first.result(timeout=10) is True
    assert processing_worker.run_once() is False  # accepted remote work retains its slot
    assert processed == [first_id]


def test_project_backlogs_rotate_using_durable_dispatch_history(library, monkeypatch):
    _, sessions, ids, _, info = setup(library, monkeypatch)
    configure_worker(monkeypatch, sessions)
    base = datetime.now(UTC) - timedelta(days=1)
    with sessions() as db:
        source = db.get(MediaVersion, uuid.UUID(info["id"]))
        other_media = LibraryMedia(project_id=ids["other"], name="Other project media")
        db.add(other_media)
        db.flush()
        queued = []
        for ordinal in range(1, 5):
            version = source if ordinal == 1 else copy_version(db, source, ordinal)
            if ordinal >= 3:
                version.media_id = other_media.id
            run = new_run(db, version, ids["user"])
            run.created_at = run.updated_at = base + timedelta(seconds=ordinal)
            queued.append(run.id)
        db.commit()
    visited = []

    class Remote:
        def get_job(self, run_id):
            visited.append(run_id)
            return {
                "status": "ready",
                "capabilities": {"visual": {"state": "ready", "vector_count": 1}},
            }

    monkeypatch.setattr(processing_worker, "wise_client", Remote)
    # Each iteration opens new DB sessions; no scheduler state in memory can
    # make the first project's older second entry jump ahead of project B.
    for _ in range(4):
        assert processing_worker.run_once() is True
    assert visited == [queued[0], queued[2], queued[1], queued[3]]
    assert processing_worker.run_once() is False
    with sessions() as db:
        rows = list(db.scalars(select(MediaProcessingRun)))
        assert all(row.status == "ready" and row.last_dispatched_at for row in rows)


def test_remote_polls_preserve_dispatch_time_and_cancel_precedes_new_work(library, monkeypatch):
    _, sessions, ids, _, info = setup(library, monkeypatch)
    configure_worker(monkeypatch, sessions)
    with sessions() as db:
        source = db.get(MediaVersion, uuid.UUID(info["id"]))
        run = new_run(db, source, ids["user"])
        db.commit()
        run_id = run.id
    visited = []

    class Remote:
        def get_job(self, current):
            visited.append(current)
            return {"status": "processing", "phase": "extracting"}

        def cancel(self, current):
            return {"status": "canceled"}

    monkeypatch.setattr(processing_worker, "wise_client", Remote)
    assert processing_worker.run_once() is True
    with sessions() as db:
        run = db.get(MediaProcessingRun, run_id)
        dispatched = run.last_dispatched_at
        assert dispatched and run.status == "waiting"
        run.next_poll_at = None
        db.commit()
    assert processing_worker.run_once() is True
    with sessions() as db:
        run = db.get(MediaProcessingRun, run_id)
        assert run.last_dispatched_at == dispatched
        run.cancel_requested, run.next_poll_at = True, None
        other = new_run(db, db.get(MediaVersion, run.version_id), ids["user"], provider="mediacms")
        other.created_at = other.updated_at = dispatched - timedelta(days=1)
        db.commit()
    assert processing_worker.run_once() is True
    assert visited == [run_id] * 3
    with sessions() as db:
        run = db.get(MediaProcessingRun, run_id)
        assert run.status == "canceled" and run.last_dispatched_at == dispatched
        assert (
            db.scalar(
                select(MediaProcessingRun).where(MediaProcessingRun.provider == "mediacms")
            ).attempts
            == 0
        )


def test_auto_admission_balances_providers_projects_and_enabled_speech(library, monkeypatch):
    _, sessions, ids, _, info = setup(library, monkeypatch)
    configure_auto(monkeypatch)
    monkeypatch.setattr(settings, "library_mediacms_transcription_enabled", True)
    with sessions() as db:
        source = db.get(MediaVersion, uuid.UUID(info["id"]))
        other_media = LibraryMedia(project_id=ids["other"], name="Newer project upload")
        db.add(other_media)
        db.flush()
        base = datetime.now(UTC) - timedelta(days=1)
        source.created_at = base
        for ordinal in range(2, 7):
            version = copy_version(db, source, ordinal)
            version.created_at = base + timedelta(seconds=ordinal)
            if ordinal == 6:
                version.media_id = other_media.id
        db.commit()
        for _ in range(4):
            assert processing.enqueue_automatic(db)
        rows = db.execute(
            select(
                MediaProcessingRun.provider,
                LibraryMedia.project_id,
                MediaProcessingRun.capabilities,
            )
            .join(MediaVersion, MediaVersion.id == MediaProcessingRun.version_id)
            .join(LibraryMedia, LibraryMedia.id == MediaVersion.media_id)
        ).all()
        assert {(provider, str(project)) for provider, project, _ in rows} == {
            (provider, str(project))
            for provider in ("wise", "mediacms")
            for project in (ids["project"], ids["other"])
        }
        assert all(
            caps == ["preview", "speech"] for provider, _, caps in rows if provider == "mediacms"
        )


def test_crash_after_remote_acceptance_keeps_slot_before_other_project(library, monkeypatch):
    _, sessions, ids, _, info = setup(library, monkeypatch)
    configure_worker(monkeypatch, sessions)
    with sessions() as db:
        source = db.get(MediaVersion, uuid.UUID(info["id"]))
        first = new_run(db, source, ids["user"])
        first.created_at = first.updated_at = datetime.now(UTC) - timedelta(seconds=1)
        other_media = LibraryMedia(project_id=ids["other"], name="Other project")
        db.add(other_media)
        db.flush()
        other_version = copy_version(db, source, 2)
        other_version.media_id = other_media.id
        second = new_run(db, other_version, ids["user"])
        db.commit()
        first_id, second_id = first.id, second.id

    class ProcessExit(BaseException):
        """Bypass Exception recovery, as a process death before checkpoint does."""

    class Remote:
        accepted = {}
        gets = []
        uploads = []

        def get_job(self, run_id):
            self.gets.append(run_id)
            return self.accepted.get(run_id)

        def submit(self, project, version, run_id, *args, **kwargs):
            self.uploads.append(run_id)
            self.accepted[run_id] = {"status": "processing", "phase": "extracting"}
            raise ProcessExit()

    remote = Remote()
    monkeypatch.setattr(processing_worker, "wise_client", lambda: remote)
    with pytest.raises(ProcessExit):
        processing_worker.run_once()
    with sessions() as db:
        first = db.get(MediaProcessingRun, first_id)
        assert first.status == "processing" and first.remote_id is None
        assert first.outputs["dispatch_retry"] and first.last_dispatched_at
        assert db.get(MediaProcessingRun, second_id).attempts == 0
    # New dispatcher/session must reconcile the same accepted ID. There is no
    # process-local scheduler state or exception handler involved in this check.
    assert processing_worker.run_once()
    assert remote.uploads == [first_id] and remote.gets == [first_id, first_id]
    assert processing_worker.run_once() is False
    with sessions() as db:
        first = db.get(MediaProcessingRun, first_id)
        assert first.status == "waiting" and "dispatch_retry" not in first.outputs
        assert db.get(MediaProcessingRun, second_id).attempts == 0


def test_explicit_retry_intent_survives_exit_after_slot_claim(library, monkeypatch):
    _, sessions, ids, _, info = setup(library, monkeypatch)
    configure_worker(monkeypatch, sessions)
    with sessions() as db:
        run = new_run(db, db.get(MediaVersion, uuid.UUID(info["id"])), ids["user"])
        run.attempts = 3  # An earlier failure was explicitly retried via the API.
        db.commit()
        run_id = run.id

    class ProcessExit(BaseException):
        pass

    class Remote:
        reads, retries = 0, 0

        def get_job(self, current):
            assert current == run_id
            self.reads += 1
            if self.reads == 1:
                raise ProcessExit()
            return {"status": "failed"}

        def retry(self, current):
            assert current == run_id
            self.retries += 1
            return {"status": "queued", "phase": "queued"}

    remote = Remote()
    monkeypatch.setattr(processing_worker, "wise_client", lambda: remote)
    with pytest.raises(ProcessExit):
        processing_worker.run_once()
    assert processing_worker.run_once()
    assert remote.retries == 1
    with sessions() as db:
        run = db.get(MediaProcessingRun, run_id)
        assert run.status == "waiting" and "dispatch_retry" not in run.outputs
