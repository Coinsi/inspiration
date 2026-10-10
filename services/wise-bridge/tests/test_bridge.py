"""Protocol/lifecycle tests with an explicit fake engine, not model-quality tests."""

import json
import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wise_bridge.api import create_app
from wise_bridge.config import Settings
from wise_bridge.query_errors import ReferenceQueryError
from wise_bridge.runner import Canceled, EngineFailure, ProcessRunner

TOKEN = "test-private-service-token-123456789"


@pytest.mark.parametrize(
    "kind,filename,allowed,denied",
    [
        ("image", "preview.png", "visual", "audio"),
        ("audio", "preview.m4a", "audio", "visual"),
    ],
)
def test_source_types_keep_payload_kind_and_reject_incompatible_capabilities(
    service, kind, filename, allowed, denied
):
    client, store, _, _ = service
    project, source, job = identifiers()
    route = f"/v1/projects/{project}/sources/{source}"
    for cap, status in [(denied, 422), (allowed, 202)]:
        response = client.post(
            route,
            data={"job_id": job, "media_type": kind, "capabilities": json.dumps([cap])},
            files={"file": (filename, b"explicit fake engine fixture")},
        )
        assert response.status_code == status, response.text
    assert store.job(job)["payload"]["media_type"] == kind


class FakeEngine:
    """Only exercises the bridge's transaction boundary; never production wiring."""

    def __init__(self):
        self.fail = False
        self.requests = []
        self.on_build = None

    def operation(self, operation, payload, *args, **kwargs):
        self.requests.append((operation, payload))
        if operation == "search":
            return [
                {
                    "source_id": sid,
                    "score": 0.5,
                    "modality": payload["modality"],
                    "start_seconds": 1,
                    "end_seconds": 2,
                }
                for sid in payload["source_ids"]
            ][: payload["limit"]]
        return {"installed": True}

    def build(self, sources, snapshot, previous, phase, canceled, operation="upsert"):
        snapshot.mkdir(parents=True)
        phase("extracting_features")
        (snapshot / "engine").mkdir()
        if self.on_build:
            self.on_build()
        if self.fail:
            raise EngineFailure("simulated failure")
        result = {
            "counts": {
                sid: {cap: 2 for cap in value["capabilities"]}
                for sid, value in sources.items()
            },
            "media_map": {},
            "models": {},
        }
        (snapshot / "manifest.json").write_text(
            json.dumps({"inspection": result, "sources": sources})
        )
        return result


@pytest.fixture
def service(tmp_path):
    engine = FakeEngine()
    settings = Settings(
        data_dir=tmp_path, token=TOKEN, worker_enabled=False, min_free_bytes=0
    )
    app = create_app(settings, engine)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        yield client, app.state.store, app.state.worker, engine


def identifiers():
    return tuple(str(uuid.uuid4()) for _ in range(3))


def upload(client, project, source, job, body=b"test proxy bytes", caps=None):
    return client.post(
        f"/v1/projects/{project}/sources/{source}",
        data={
            "job_id": job,
            "media_type": "video",
            "capabilities": json.dumps(caps or ["visual", "audio"]),
        },
        files={"file": ("proxy.mp4", body, "video/mp4")},
    )


def work(store, worker):
    job = store.claim()
    assert job is not None
    worker.process(job)
    return store.job(job["id"])


def test_auth_and_health_do_not_claim_models_ready(service):
    client, _, _, _ = service
    assert (
        client.get("/health", headers={"Authorization": "Bearer bad"}).status_code
        == 401
    )
    body = client.get("/health").json()
    assert body["models_verified"] is False
    assert body["upstream_installation_verified"] is False
    assert body["capabilities"]["face"]["configured"] is False


@pytest.mark.parametrize("code", ["reference_no_object", "reference_region_no_match"])
def test_expected_reference_error_is_http422_not_internal_diagnostic(
    service, monkeypatch, code
):
    client, _, worker, _ = service

    def reject(*args):
        raise ReferenceQueryError(code)

    # The HTTP boundary is tested separately from real OWLv2; it must never
    # reinterpret this typed error as a provider outage or an empty match list.
    monkeypatch.setattr(worker, "search", reject)
    response = client.post(
        f"/v1/projects/{uuid.uuid4()}/search",
        json={"query": "cup", "modality": "visual"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": code}}


def test_process_runner_preserves_typed_reference_error(tmp_path, monkeypatch):
    runner = ProcessRunner(Settings(data_dir=tmp_path, token=TOKEN, min_free_bytes=0))

    def reject(*args):
        raise ReferenceQueryError("reference_no_object")

    monkeypatch.setattr(runner.query_process, "request", reject)
    with pytest.raises(ReferenceQueryError, match="reference_no_object"):
        runner.operation("search", {}, tmp_path)


def test_idempotency_and_immutable_versions(service):
    client, store, worker, _ = service
    project, source, job = identifiers()
    assert upload(client, project, source, job).json()["status"] == "queued"
    assert upload(client, project, source, job).status_code == 202
    assert upload(client, project, source, job, b"different").status_code == 409
    assert (
        upload(client, project, source, str(uuid.uuid4()), b"different").status_code
        == 409
    )
    work(store, worker)
    body = client.get(f"/v1/jobs/{job}").json()
    assert body["status"] == "ready"
    assert body["capabilities"]["visual"]["vector_count"] == 2
    assert "file" not in json.dumps(body)


def test_same_id_cannot_cross_project(service):
    client, _, _, _ = service
    project, source, job = identifiers()
    assert upload(client, project, source, job).status_code == 202
    assert upload(client, str(uuid.uuid4()), source, job).status_code == 409


def test_failed_rebuild_keeps_published_snapshot_and_retry_recovers(service):
    client, store, worker, engine = service
    project, source, job = identifiers()
    upload(client, project, source, job)
    work(store, worker)
    previous = store.head(project)
    second, other_job = str(uuid.uuid4()), str(uuid.uuid4())
    upload(client, project, second, other_job)
    engine.fail = True
    assert work(store, worker)["state"] == "failed"
    assert store.head(project) == previous
    assert second not in store.sources(project)
    assert client.get(f"/v1/jobs/{other_job}").json()["error"] == "simulated failure"
    engine.fail = False
    assert client.post(f"/v1/jobs/{other_job}/retry").json()["status"] == "queued"
    assert work(store, worker)["state"] == "ready"
    assert set(store.sources(project)) == {source, second}


def test_cancel_during_build_never_publishes(service):
    client, store, worker, engine = service
    project, source, job = identifiers()
    upload(client, project, source, job)
    engine.on_build = lambda: store.cancel(job)
    assert work(store, worker)["state"] == "canceled"
    assert store.head(project) is None


def test_deletion_tombstone_filters_immediately_even_if_rebuild_fails(service):
    client, store, worker, engine = service
    project, source, job = identifiers()
    upload(client, project, source, job)
    work(store, worker)
    delete_job = str(uuid.uuid4())
    response = client.delete(
        f"/v1/projects/{project}/sources/{source}", params={"job_id": delete_job}
    )
    assert response.status_code == 202
    assert not store.sources(project)
    engine.fail = True
    work(store, worker)
    assert (
        client.post(f"/v1/projects/{project}/search", json={"query": "rain"}).json()[
            "items"
        ]
        == []
    )
    assert upload(client, project, source, str(uuid.uuid4())).status_code == 409


def test_search_whitelist_is_passed_to_engine_before_top_k(service):
    client, store, worker, engine = service
    project, first, job = identifiers()
    upload(client, project, first, job)
    work(store, worker)
    second = str(uuid.uuid4())
    upload(client, project, second, str(uuid.uuid4()))
    work(store, worker)
    unknown = str(uuid.uuid4())
    response = client.post(
        f"/v1/projects/{project}/search",
        json={"query": "rain", "source_ids": [second, unknown], "limit": 1},
    )
    assert response.status_code == 200
    assert [row["source_id"] for row in response.json()["items"]] == [second]
    assert engine.requests[-1][1]["source_ids"] == [second]
    assert (
        client.post(
            f"/v1/projects/{uuid.uuid4()}/search",
            json={"query": "rain", "source_ids": [second]},
        ).json()["items"]
        == []
    )


def test_media_query_cleans_reference_file(service):
    client, store, worker, engine = service
    project, source, job = identifiers()
    upload(client, project, source, job)
    work(store, worker)
    response = client.post(
        f"/v1/projects/{project}/search/media",
        files={"file": ("reference.png", b"test image")},
        data={"modality": "visual", "source_ids": json.dumps([source])},
    )
    assert response.status_code == 200
    assert not Path(engine.requests[-1][1]["query_file"]).exists()


def test_interrupted_job_recovers_after_lease_restart(service):
    client, store, _, _ = service
    project, source, job = identifiers()
    upload(client, project, source, job)
    assert store.claim()["state"] == "processing"
    store.recover()
    assert store.job(job)["state"] == "queued"


def test_unconfigured_optional_capability_and_unknown_jobs_are_explicit(service):
    client, _, _, _ = service
    project, source, job = identifiers()
    assert upload(client, project, source, job, caps=["face"]).status_code == 422
    assert client.get(f"/v1/jobs/{job}").status_code == 404
    assert (
        client.post(
            f"/v1/projects/{project}/search", json={"query": "face", "modality": "face"}
        ).status_code
        == 422
    )


def test_resource_limit_removes_incomplete_upload(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        token=TOKEN,
        worker_enabled=False,
        min_free_bytes=0,
        max_file_bytes=3,
    )
    with TestClient(
        create_app(settings, FakeEngine()), headers={"Authorization": "Bearer " + TOKEN}
    ) as client:
        project, source, job = identifiers()
        assert upload(client, project, source, job).status_code == 413
        assert not list((tmp_path / "sources").rglob("*.upload"))
        assert not list((tmp_path / "sources").rglob("*.mp4"))


def test_production_runner_invokes_real_pinned_wise_cli(tmp_path):
    settings = Settings(data_dir=tmp_path, token=TOKEN)
    runner = ProcessRunner(settings)
    calls = []
    runner.run = lambda args, *rest: calls.append(args)
    runner.operation = lambda *args, **kwargs: {
        "counts": {"source": {"visual": 1}},
        "models": {},
        "media_map": {},
    }
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake")
    runner.build(
        {
            "source": {
                "file": str(source),
                "capabilities": ["visual"],
                "media_type": "video",
            }
        },
        tmp_path / "snapshot",
        None,
        lambda phase: None,
        lambda: False,
    )
    assert calls[0][:3] == ["-m", "wise", "extract-features"]
    assert "--num-workers" in calls[0]
    assert calls[1][:3] == ["-m", "wise", "create-index"]
    assert "--overwrite" in calls[1]


def test_adding_capability_never_loses_a_previously_ready_one(service):
    client, store, worker, _ = service
    project, source, job = identifiers()
    upload(client, project, source, job, caps=["visual", "audio"])
    work(store, worker)
    upload(client, project, source, str(uuid.uuid4()), caps=["visual"])
    work(store, worker)
    assert set(store.sources(project)[source]["capabilities"]) == {"visual", "audio"}


def test_successful_delete_releases_proxy_and_old_snapshots(service):
    client, store, worker, _ = service
    project, source, job = identifiers()
    upload(client, project, source, job)
    work(store, worker)
    path = Path(store.sources(project)[source]["file"])
    delete_job = str(uuid.uuid4())
    client.delete(
        f"/v1/projects/{project}/sources/{source}", params={"job_id": delete_job}
    )
    assert client.post(f"/v1/jobs/{delete_job}/cancel").status_code == 409
    assert work(store, worker)["state"] == "ready"
    assert not path.exists()
    snapshots = worker.settings.data_dir / "projects" / project / "snapshots"
    assert len(list(snapshots.iterdir())) == 1


def test_real_subprocess_is_terminated_on_cancellation(tmp_path):
    settings = Settings(
        data_dir=tmp_path, token=TOKEN, python=sys.executable, min_free_bytes=0
    )
    runner = ProcessRunner(settings)
    started = time.monotonic()
    with pytest.raises(Canceled):
        runner.run(
            ["-c", "import time; time.sleep(30)"],
            tmp_path / "job",
            lambda: time.monotonic() - started > 0.3,
        )
    assert time.monotonic() - started < 12


def test_repeated_delete_uses_real_empty_snapshot_builder(service):
    client, store, worker, engine = service
    project, source, job = identifiers()
    upload(client, project, source, job)
    work(store, worker)
    proxy = Path(store.sources(project)[source]["file"])
    # Keep the install probe isolated, but exercise the production builder, not
    # FakeEngine's unconditional engine mkdir which masked this regression.
    runner = ProcessRunner(worker.settings)
    runner.operation = engine.operation
    runner.run = lambda *args, **kwargs: pytest.fail("Empty cleanup invoked WISE")
    worker.runner = runner
    for _ in range(3):
        deletion = str(uuid.uuid4())
        response = client.delete(
            f"/v1/projects/{project}/sources/{source}",
            params={"job_id": deletion},
        )
        assert response.status_code == 202
        assert work(store, worker)["state"] == "ready"
        snapshots = worker.settings.data_dir / "projects" / project / "snapshots"
        current = snapshots / store.head(project)
        assert list(snapshots.iterdir()) == [current]
        assert not (current / "engine").exists()
        assert json.loads((current / "manifest.json").read_text())["sources"] == {}
        assert not proxy.exists()
        assert not store.sources(project)


def test_build_unloads_queries_before_probe_and_search_uses_new_revision(service):
    client, store, worker, engine = service
    order = []
    operation = engine.operation

    def observed_operation(name, *args, **kwargs):
        order.append(name)
        return operation(name, *args, **kwargs)

    engine.operation = observed_operation
    engine.close_queries = lambda: order.append("close")
    project, source, job = identifiers()
    upload(client, project, source, job)
    work(store, worker)
    assert order[:2] == ["close", "probe"]
    first_revision = store.head(project)
    client.post(f"/v1/projects/{project}/search", json={"query": "first"})
    assert engine.requests[-1][1]["revision"] == first_revision
    second = str(uuid.uuid4())
    upload(client, project, second, str(uuid.uuid4()))
    order.clear()
    work(store, worker)
    assert order[:2] == ["close", "probe"]
    client.post(f"/v1/projects/{project}/search", json={"query": "second"})
    assert engine.requests[-1][1]["revision"] == store.head(project) != first_revision
    assert engine.requests[-1][1]["project_id"] == project


def test_deletion_during_query_is_filtered_after_worker_response(service):
    client, store, worker, engine = service
    project, source, job = identifiers()
    upload(client, project, source, job)
    work(store, worker)
    operation = engine.operation

    def delete_while_searching(name, *args, **kwargs):
        result = operation(name, *args, **kwargs)
        if name == "search":
            store.enqueue(str(uuid.uuid4()), project, source, "delete", {})
        return result

    engine.operation = delete_while_searching
    response = client.post(f"/v1/projects/{project}/search", json={"query": "rain"})
    assert response.status_code == 200
    assert response.json()["items"] == []
