"""Real API/DB lifecycle anchors with an explicit non-inference WISE double."""

# ruff: noqa: F811
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import ProviderError
from app.models.media_processing import MediaProcessingRun
from app.modules.library import processing_worker
from tests.test_library import library, media_studio  # noqa: F401
from tests.test_media_processing import setup
from tests.test_media_workflow import post_ok


def add_history(sessions, ids, version):
    with sessions() as db:
        rows = []
        for age, status, attempts in [(3, "ready", 1), (2, "failed", 1), (1, "canceled", 0)]:
            run = MediaProcessingRun(
                version_id=uuid.UUID(version["id"]),
                created_by=ids["user"],
                provider="wise",
                operation="process",
                capabilities=["visual"],
                source_hash=version["proxy_hash"],
                status=status,
                attempts=attempts,
                created_at=datetime.now(UTC) - timedelta(minutes=age),
            )
            db.add(run)
            db.flush()
            rows.append(run.id)
        db.commit()
    return rows


def cleanup_for(sessions, version):
    with sessions() as db:
        return db.scalar(
            select(MediaProcessingRun.id).where(
                MediaProcessingRun.version_id == uuid.UUID(version["id"]),
                MediaProcessingRun.provider == "wise",
                MediaProcessingRun.operation == "delete",
            )
        )


def test_cleanup_falls_back_to_real_older_remote_job_and_checkpoints_before_delete(
    library, monkeypatch
):
    client, sessions, ids, base, version = setup(library, monkeypatch)
    old, not_received, never_attempted = add_history(sessions, ids, version)
    post_ok(client, f"{base}/{version['media_id']}/trash")
    cleanup_id = cleanup_for(sessions, version)
    with sessions() as db:
        run = db.get(MediaProcessingRun, cleanup_id)
        assert run.outputs["source_job_ids"] == [str(not_received), str(old)]
        assert str(never_attempted) not in run.outputs["source_job_ids"]

    class Remote:
        queries = []
        deletes = 0

        def get_job(self, job_id):
            self.queries.append(str(job_id))
            if str(job_id) == str(old):
                return {"job_id": str(old), "status": "ready"}
            assert str(job_id) in (str(not_received), str(cleanup_id))
            return None

        def delete(self, project_id, source_id, job_id, *, source_job_id=None):
            assert str(project_id) == ids["project"] and str(source_id) == version["id"]
            assert job_id == cleanup_id and source_job_id == str(old)
            with sessions() as db:
                assert db.get(MediaProcessingRun, cleanup_id).outputs["source_job_id"] == str(old)
            self.deletes += 1
            if self.deletes == 1:
                raise ProviderError("simulated transport timeout", {"retryable": True})
            return {"status": "ready", "phase": "ready"}

    remote = Remote()
    monkeypatch.setattr(processing_worker, "wise_client", lambda: remote)
    processing_worker.process(cleanup_id)
    assert remote.queries == [str(cleanup_id), str(not_received), str(old)]
    with sessions() as db:
        assert db.get(MediaProcessingRun, cleanup_id).status == "waiting"
    processing_worker.process(cleanup_id)
    assert remote.queries == [str(cleanup_id), str(not_received), str(old), str(cleanup_id)]
    with sessions() as db:
        assert db.get(MediaProcessingRun, cleanup_id).status == "ready"


def test_restore_anchor_is_persisted_and_sent_when_reindexing(library, monkeypatch):
    client, sessions, ids, base, version = setup(library, monkeypatch)
    add_history(sessions, ids, version)
    post_ok(client, f"{base}/{version['media_id']}/trash")
    cleanup_id = cleanup_for(sessions, version)
    with sessions() as db:
        db.get(MediaProcessingRun, cleanup_id).status = "ready"
        db.add(
            MediaProcessingRun(
                version_id=uuid.UUID(version["id"]),
                created_by=ids["user"],
                provider="wise",
                operation="delete",
                capabilities=[],
                source_hash=version["proxy_hash"],
                status="ready",
                created_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        db.commit()
    post_ok(client, f"{base}/{version['media_id']}/restore")
    result = post_ok(
        client,
        f"{base}/versions/{version['id']}/process",
        {"provider": "wise", "capabilities": ["visual"]},
    )
    run_id = uuid.UUID(result["id"])
    with sessions() as db:
        assert db.get(MediaProcessingRun, run_id).outputs["restore_after_job_id"] == str(cleanup_id)
    assert "restore_after_job_id" not in result

    class Remote:
        def get_job(self, job_id):
            assert job_id == run_id
            return None

        def submit(
            self,
            project_id,
            source_id,
            job_id,
            path,
            capabilities,
            *,
            canceled,
            restore_after_job_id=None,
            media_type=None,
            mime_type=None,
        ):
            assert str(project_id) == ids["project"] and str(source_id) == version["id"]
            assert job_id == run_id and path.is_file() and not canceled()
            assert capabilities == ["visual"] and restore_after_job_id == str(cleanup_id)
            assert media_type == "video" and mime_type == "video/mp4"
            return {"status": "queued", "phase": "queued"}

    monkeypatch.setattr(processing_worker, "wise_client", Remote)
    processing_worker.process(run_id)
    with sessions() as db:
        actual = db.get(MediaProcessingRun, run_id)
        assert actual.status == "waiting" and actual.remote_id == str(run_id)
        assert actual.outputs["restore_after_job_id"] == str(cleanup_id)


@pytest.mark.parametrize("known_candidates", [False, True])
def test_cleanup_with_no_received_source_uses_the_initial_generation_guard(
    library, monkeypatch, known_candidates
):
    client, sessions, ids, base, version = setup(library, monkeypatch)
    add_history(sessions, ids, version)
    post_ok(client, f"{base}/{version['media_id']}/trash")
    cleanup_id = cleanup_for(sessions, version)
    if not known_candidates:
        with sessions() as db:
            db.get(MediaProcessingRun, cleanup_id).outputs = {}
            db.commit()

    class Remote:
        def get_job(self, _job_id):
            return None

        def delete(self, project_id, source_id, job_id, *, source_job_id=None):
            assert str(project_id) == ids["project"] and str(source_id) == version["id"]
            assert job_id == cleanup_id and source_job_id is None
            return {"status": "ready", "phase": "ready"}

    monkeypatch.setattr(processing_worker, "wise_client", Remote)
    processing_worker.process(cleanup_id)
    with sessions() as db:
        assert db.get(MediaProcessingRun, cleanup_id).status == "ready"
