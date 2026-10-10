"""Trash outbox and retry contracts against an isolated real PostgreSQL schema."""

# ruff: noqa: F811
import uuid

import pytest
from sqlalchemy import select

from app.models.library import LibraryMedia
from app.models.media_processing import MediaProcessingRun
from app.modules.library import processing_mediacms, processing_worker, service, wise_search
from app.modules.library.providers.mediacms import MediaCMSError, token_for_upload
from tests.test_library import library, media_studio  # noqa: F401
from tests.test_media_processing import setup
from tests.test_media_workflow import post_ok


def histories(sessions, ids, version):
    with sessions() as db:
        rows = []
        for provider, status, attempts, remote_id, outputs in [
            ("mediacms", "failed", 1, "temporaryA", {}),
            ("mediacms", "canceled", 1, None, {}),
            ("mediacms", "ready", 1, "alreadyRemoved", {"upstream_removed": True}),
            ("mediacms", "canceled", 0, None, {}),
            (
                "wise",
                "ready",
                1,
                None,
                {
                    "capabilities": {
                        "visual": {"state": "ready", "vector_count": 3},
                    }
                },
            ),
        ]:
            row = MediaProcessingRun(
                version_id=uuid.UUID(version["id"]),
                created_by=ids["user"],
                provider=provider,
                operation="process",
                capabilities=["visual"] if provider == "wise" else ["preview"],
                source_hash=version["original_hash"],
                status=status,
                attempts=attempts,
                remote_id=remote_id,
                outputs=outputs,
            )
            db.add(row)
            db.flush()
            rows.append(row)
        db.commit()
        return {"cms_ids": {"temporaryA", token_for_upload(str(rows[1].id))}, "wise": rows[-1].id}


@pytest.mark.parametrize("temporary_failure", [False, True])
def test_trash_groups_cms_copies_and_cleanup_remains_scoped_and_retryable(
    library, monkeypatch, temporary_failure
):
    client, sessions, ids, base, version = setup(library, monkeypatch)
    history = histories(sessions, ids, version)
    post_ok(client, f"{base}/{version['media_id']}/trash")
    with sessions() as db:
        jobs = list(
            db.scalars(
                select(MediaProcessingRun).where(
                    MediaProcessingRun.operation == "delete",
                )
            )
        )
        assert len(jobs) == 2
        cms = next(job for job in jobs if job.provider == "mediacms")
        wise = next(job for job in jobs if job.provider == "wise")
        assert set(cms.outputs["remote_ids"]) == history["cms_ids"]
        cms_id, wise_id = cms.id, wise.id
    listing = client.get(f"{base}/processing/runs?version_id={version['id']}")
    assert listing.status_code == 200, listing.text
    assert {item["id"] for item in listing.json()["items"]} == {str(cms_id), str(wise_id)}
    assert "temporaryA" not in listing.text
    assert client.post(f"{base}/processing/runs/{cms_id}/cancel").status_code == 409
    other = f"/api/v1/projects/{ids['other']}/library"
    assert client.post(f"{other}/processing/runs/{cms_id}/retry").status_code == 403

    class CMS:
        calls = []
        failed = False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_media(self, *_args, **_kwargs):
            return object()

        def manifest(self, _identity):
            return {"processing": {"complete": True}}

        def get_transcription(self, _identity):
            return {"status": "not_requested"}

        def delete_media(self, remote_id):
            assert remote_id in history["cms_ids"]
            self.calls.append(remote_id)
            if len(self.calls) == 2 and not self.failed:
                self.failed = True
                raise MediaCMSError(
                    "deliberate delete failure",
                    status_code=None if temporary_failure else 403,
                    retryable=temporary_failure,
                )
            return True

    cms_remote = CMS()
    monkeypatch.setattr(processing_mediacms, "client", lambda: cms_remote)
    processing_worker.process(cms_id)
    with sessions() as db:
        run = db.get(MediaProcessingRun, cms_id)
        assert run.status == ("waiting" if temporary_failure else "failed")
        if temporary_failure:
            assert run.progress["phase"] == "reconciling"
        assert db.get(LibraryMedia, uuid.UUID(version["media_id"])).deleted_at is not None
    if not temporary_failure:
        assert post_ok(client, f"{base}/processing/runs/{cms_id}/retry")["status"] == "queued"
    processing_worker.process(cms_id)
    with sessions() as db:
        assert db.get(MediaProcessingRun, cms_id).status == "ready"
    assert set(cms_remote.calls) == history["cms_ids"]

    class WISE:
        def get_job(self, job_id):
            if str(job_id) == str(history["wise"]):
                return {"job_id": str(job_id), "status": "ready"}
            assert job_id == wise_id
            return None

        def delete(self, project_id, version_id, job_id, *, source_job_id=None):
            assert str(project_id) == ids["project"]
            assert str(version_id) == version["id"]
            assert job_id == wise_id
            assert source_job_id == str(history["wise"])
            return {"status": "ready", "phase": "ready"}

    monkeypatch.setattr(processing_worker, "wise_client", WISE)
    processing_worker.process(wise_id)
    with sessions() as db:
        assert db.get(MediaProcessingRun, wise_id).status == "ready"


def test_cleanup_waits_for_encoding_and_speech_then_resumes_without_repeating_deletes(
    library, monkeypatch
):
    client, sessions, ids, base, version = setup(library, monkeypatch)
    history = histories(sessions, ids, version)
    post_ok(client, f"{base}/{version['media_id']}/trash")
    with sessions() as db:
        run_id = db.scalar(
            select(MediaProcessingRun.id).where(
                MediaProcessingRun.operation == "delete",
                MediaProcessingRun.provider == "mediacms",
            )
        )

    class Remote:
        complete = False
        speech = "running"
        deleted = []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_media(self, identity, **_kwargs):
            return None if identity in self.deleted else object()

        def manifest(self, _identity):
            return {"processing": {"complete": self.complete}}

        def get_transcription(self, _identity):
            return {"status": self.speech}

        def delete_media(self, identity):
            assert self.complete and self.speech == "success"
            self.deleted.append(identity)
            return True

    remote = Remote()
    monkeypatch.setattr(processing_mediacms, "client", lambda: remote)
    processing_worker.process(run_id)
    with sessions() as db:
        run = db.get(MediaProcessingRun, run_id)
        assert run.status == "waiting" and run.progress["phase"] == "canceling"
    assert not remote.deleted
    remote.complete = True
    processing_worker.process(run_id)
    assert not remote.deleted
    remote.speech = "success"
    processing_worker.process(run_id)
    with sessions() as db:
        run = db.get(MediaProcessingRun, run_id)
        assert run.status == "ready"
        assert set(run.outputs["removed_ids"]) == history["cms_ids"]
    processing_worker.process(run_id)
    assert len(remote.deleted) == len(history["cms_ids"])


def test_trash_and_cleanup_enqueue_rollback_together(library, monkeypatch):
    client, sessions, ids, base, version = setup(library, monkeypatch)
    histories(sessions, ids, version)
    original_audit = service.audit.record

    def failing_audit(*args, **kwargs):
        if kwargs.get("action") == "library.trash":
            raise RuntimeError("Test transaction rollback")
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(service.audit, "record", failing_audit)
    with pytest.raises(RuntimeError, match="Test transaction rollback"):
        client.post(f"{base}/{version['media_id']}/trash")
    with sessions() as db:
        assert db.get(LibraryMedia, uuid.UUID(version["media_id"])).deleted_at is None
        assert not list(
            db.scalars(
                select(MediaProcessingRun).where(
                    MediaProcessingRun.operation == "delete",
                )
            )
        )


def test_restored_media_does_not_reuse_pre_delete_search_index(library, monkeypatch):
    client, sessions, ids, base, version = setup(library, monkeypatch)
    histories(sessions, ids, version)
    post_ok(client, f"{base}/{version['media_id']}/trash")
    post_ok(client, f"{base}/{version['media_id']}/restore")

    def unavailable_remote(*args):
        raise AssertionError("A restored but deleted index must not be queried")

    monkeypatch.setattr(wise_search, "WiseClient", unavailable_remote)
    result = post_ok(
        client,
        f"{base}/search",
        {
            "engine": "wise",
            "target": "visual",
            "query": "旧画面",
        },
    )
    assert result["items"] == []
    assert result["coverage"]["indexed"] == 0
    assert result["coverage"]["unindexed"] == 1


@pytest.mark.parametrize("cleanup_status", ["queued", "waiting", "failed"])
def test_restored_media_cannot_reindex_until_previous_cleanup_is_complete(
    library, monkeypatch, cleanup_status
):
    client, sessions, ids, base, version = setup(library, monkeypatch)
    history = histories(sessions, ids, version)
    post_ok(client, f"{base}/{version['media_id']}/trash")
    post_ok(client, f"{base}/{version['media_id']}/restore")
    with sessions() as db:
        cleanup = db.scalar(
            select(MediaProcessingRun).where(
                MediaProcessingRun.operation == "delete",
                MediaProcessingRun.provider == "wise",
            )
        )
        cleanup.status = cleanup_status
        cleanup_id = cleanup.id
        db.commit()
    endpoint = f"{base}/versions/{version['id']}/process"
    payload = {"provider": "wise", "capabilities": ["visual"]}
    rejected = client.post(endpoint, json=payload)
    assert rejected.status_code == 409, rejected.text
    assert "清理" in rejected.text
    with sessions() as db:
        processes = list(
            db.scalars(
                select(MediaProcessingRun).where(
                    MediaProcessingRun.provider == "wise",
                    MediaProcessingRun.operation == "process",
                )
            )
        )
        assert [run.id for run in processes] == [history["wise"]]
        cleanup = db.get(MediaProcessingRun, cleanup_id)
        assert cleanup.status == cleanup_status
        # The API may admit a fresh generation only after upstream cleanup finishes.
        cleanup.status = "ready"
        db.commit()
    resumed = post_ok(client, endpoint, payload)
    assert resumed["id"] != str(history["wise"])
    assert resumed["status"] == "queued" and resumed["operation"] == "process"
