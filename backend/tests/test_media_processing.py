"""Provider state/ownership tests in isolated PostgreSQL schemas; no remote inference."""

# ruff: noqa: F811
import uuid

from app.core.config import settings
from app.models.media_processing import MediaProcessingRun
from app.modules.library import processing_worker
from tests.test_library import library, media_studio, upload  # noqa: F401
from tests.test_media_workflow import post_ok


def setup(library, monkeypatch):
    client, sessions, ids, content = library
    base = f"/api/v1/projects/{ids['project']}/library"
    v = upload(client, base, content)
    monkeypatch.setattr(settings, "library_processing_enabled", True)
    monkeypatch.setattr(settings, "library_wise_url", "http://wise.test")
    monkeypatch.setattr(settings, "library_wise_token", "unit-test-token")
    monkeypatch.setattr(settings, "library_wise_capabilities", ["visual", "audio"])
    monkeypatch.setattr(processing_worker, "SessionLocal", sessions)
    return client, sessions, ids, base, v


def test_task_permissions_dedup_cancel_and_deletion(library, monkeypatch):
    client, sessions, ids, base, v = setup(library, monkeypatch)
    payload = {"provider": "wise", "capabilities": ["visual"]}
    run = post_ok(client, f"{base}/versions/{v['id']}/process", payload)
    assert post_ok(client, f"{base}/versions/{v['id']}/process", payload)["id"] == run["id"]
    assert (
        client.post(
            f"{base}/versions/{v['id']}/process", json={**payload, "capabilities": ["audio"]}
        ).status_code
        == 409
    )
    assert client.post(f"{base}/{v['media_id']}/trash").status_code == 409
    other = f"/api/v1/projects/{ids['other']}/library"
    assert client.post(f"{other}/processing/runs/{run['id']}/cancel").status_code == 403
    canceled = post_ok(client, f"{base}/processing/runs/{run['id']}/cancel")
    assert canceled["status"] == "canceled"
    assert "remote_id" not in canceled and "outputs" not in canceled
    assert post_ok(client, f"{base}/{v['media_id']}/trash")["physical_delete"] is False


def test_provider_config_is_not_reported_as_verified(library, monkeypatch):
    client, _, _, base, v = setup(library, monkeypatch)
    data = client.get(f"{base}/processing/providers").json()
    assert data["providers"][0]["configured"] is True
    assert "unit-test-token" not in str(data) and "wise.test" not in str(data)
    monkeypatch.setattr(settings, "library_wise_token", None)
    assert (
        client.post(
            f"{base}/versions/{v['id']}/process",
            json={"provider": "wise", "capabilities": ["visual"]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{base}/versions/{v['id']}/process",
            json={"provider": "mediacms", "capabilities": ["face"]},
        ).status_code
        == 422
    )


def test_reconciles_remote_job_after_timeout_without_reupload(library, monkeypatch):
    client, sessions, ids, base, v = setup(library, monkeypatch)
    run = post_ok(
        client,
        f"{base}/versions/{v['id']}/process",
        {"provider": "wise", "capabilities": ["visual"]},
    )

    class Remote:
        calls = 0

        def get_job(self, job_id):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("do-not-leak-private-url")
            return {
                "status": "ready",
                "phase": "ready",
                "capabilities": {
                    "visual": {"state": "ready", "vector_count": 4},
                },
            }

        def submit(self, *args):
            raise AssertionError("Remote job already exists; never upload again")

    remote = Remote()
    monkeypatch.setattr(processing_worker, "wise_client", lambda: remote)
    processing_worker.process(uuid.UUID(run["id"]))
    failed = client.get(f"{base}/processing/runs").json()["items"][0]
    assert failed["status"] == "failed"
    assert "private-url" not in str(failed)
    post_ok(client, f"{base}/processing/runs/{run['id']}/retry")
    processing_worker.process(uuid.UUID(run["id"]))
    with sessions() as db:
        actual = db.get(MediaProcessingRun, uuid.UUID(run["id"]))
        assert actual.status == "ready" and actual.remote_id == run["id"]


def test_cancel_in_flight_waits_for_remote_acknowledgment(library, monkeypatch):
    client, sessions, ids, base, v = setup(library, monkeypatch)
    run = post_ok(
        client,
        f"{base}/versions/{v['id']}/process",
        {"provider": "wise", "capabilities": ["visual"]},
    )
    with sessions() as db:
        actual = db.get(MediaProcessingRun, uuid.UUID(run["id"]))
        actual.status, actual.remote_id = "waiting", run["id"]
        db.commit()
    assert post_ok(client, f"{base}/processing/runs/{run['id']}/cancel")["status"] == "waiting"

    class Remote:
        def get_job(self, job_id):
            return {"status": "processing"}

        def cancel(self, job_id):
            return {"status": "canceled"}

    monkeypatch.setattr(processing_worker, "wise_client", Remote)
    processing_worker.process(uuid.UUID(run["id"]))
    assert client.get(f"{base}/processing/runs").json()["items"][0]["status"] == "canceled"
