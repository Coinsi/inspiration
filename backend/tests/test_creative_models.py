"""Exercise the real API, isolated Postgres, quota and durable generation executor."""

# ruff: noqa: F811
import json
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.adapters.generation.mock import MockProvider
from app.core.errors import ProviderError
from app.models.generation import GenerationJob, ModelChannel, ProviderConfig, Quota
from app.models.identity import Membership
from app.modules.generation import jobs
from app.modules.generation import model_catalog as catalog
from app.modules.generation.model_routing import SubmissionRejected
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import add, setup


def group(client, root, profiles, **options):
    body = {
        "name": "统一图片模型",
        "modality": "image",
        "routes": [
            {"profile_id": p["id"], "priority": 100 - i, "weight": 100}
            for i, p in enumerate(profiles)
        ],
        **options,
    }
    response = client.post(root + "/creative-models", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def pair(studio, **options):
    client, _, ids = studio
    root, one = setup(client, ids["project"], name="主渠道")
    _, two = setup(client, ids["project"], name="备用渠道")
    profiles = [
        add(client, root, one, protocol="mock", model="one", **options),
        add(client, root, two, protocol="mock", model="two", **options),
    ]
    return client, root, profiles


def generate(client, root, ids, model, **options):
    r = client.post(
        root + f"/shots/{ids['shot']}/generate",
        json={"provider": model["provider_name"], **options},
    )
    assert r.status_code == 200, r.text
    return client.get(root + f"/jobs/{r.json()['id']}").json()


def test_catalog_adoption_scoping_conflicts_and_secret_boundary(studio):
    client, sessions, ids = studio
    _, root, profiles = pair(studio)
    m = group(client, root, profiles, is_default=True)
    public = client.get(root + "/providers").json()
    visible = [p for p in public if not p["config"].get("hidden_from_catalog")]
    assert [p["provider_name"] for p in visible] == [m["provider_name"]]
    assert visible[0]["display_name"] == m["name"]
    assert visible[0]["config"]["is_default"]
    assert "test-secret" not in json.dumps(public)
    assert client.get(root + "/creative-models").json()[0]["available_routes"] == 2
    assert client.post(root + "/creative-models/import-profiles").json() == {"imported": 0}
    invalid = {"name": "x", "modality": "image", "routes": [{"profile_id": str(uuid.uuid4())}]}
    assert client.post(root + "/creative-models", json=invalid).status_code == 404
    assert client.get(f"/api/v1/projects/{ids['other']}/creative-models").status_code == 403
    update = {
        "name": "新名称",
        "modality": "image",
        "routes": [{"profile_id": profiles[0]["id"]}],
        "revision": m["revision"],
    }
    assert client.put(root + f"/creative-models/{m['id']}", json=update).status_code == 200
    assert client.put(root + f"/creative-models/{m['id']}", json=update).status_code == 409
    # Explicit import only fills the now-unbound profile, and is repeatable.
    assert client.post(root + "/creative-models/import-profiles").json() == {"imported": 1}
    assert client.post(root + "/creative-models/import-profiles").json() == {"imported": 0}
    with sessions() as db:
        db.scalar(select(Membership).where(Membership.project_id == ids["project"])).role = "viewer"
        db.commit()
    assert client.get(root + "/creative-models").status_code == 403
    assert client.post(root + "/creative-models", json=invalid).status_code == 403
    assert client.get(root + "/providers").status_code == 200


def test_priority_capability_matching_and_simulation_has_no_upstream(studio, monkeypatch):
    client, _, ids = studio
    root, c = setup(client, ids["project"])
    first = add(
        client, root, c, protocol="mock", parameters={"size": ["1024x1024"], "quality": ["low"]}
    )
    second = add(
        client,
        root,
        c,
        protocol="mock",
        model="two",
        parameters={"size": ["512x512"], "quality": ["high"]},
    )
    m = group(client, root, [first, second])
    monkeypatch.setattr(
        MockProvider, "submit", lambda *_: pytest.fail("Simulation contacted provider")
    )
    probe = root + f"/creative-models/{m['id']}/simulate"
    response = client.post(
        probe, json={"request_type": "image", "prompt": "", "provider_params": {"size": "512x512"}}
    )
    assert response.status_code == 200, response.text
    assert response.json()["candidates"][0]["profile_id"] == second["id"]
    # A union of dropdown values must never imply an unsupported combination works.
    assert (
        client.post(
            probe,
            json={
                "request_type": "image",
                "prompt": "",
                "provider_params": {"size": "512x512", "quality": "low"},
            },
        ).status_code
        == 422
    )
    response = client.post(probe, json={"request_type": "image", "prompt": ""})
    assert [c["profile_id"] for c in response.json()["candidates"]] == [first["id"], second["id"]]


def test_weighted_order_only_within_priority(monkeypatch):
    candidates = [
        {"id": "a", "priority": 100, "weight": 1},
        {"id": "b", "priority": 100, "weight": 9},
        {"id": "c", "priority": 10, "weight": 9999},
    ]
    monkeypatch.setattr(catalog.secrets, "randbelow", lambda n: n - 1)
    assert [c["id"] for c in catalog.ordered(candidates)] == ["b", "a", "c"]


def test_actual_route_price_provenance_and_explicit_rejection_failover(studio, monkeypatch):
    client, sessions, ids = studio
    _, root, profiles = pair(studio)
    with sessions() as db:
        for profile, price in zip(profiles, [7, 3], strict=True):
            row = db.get(ProviderConfig, uuid.UUID(profile["id"]))
            row.config = {**row.config, "price": {"mode": "per_request", "amount": str(price)}}
        db.add(Quota(project_id=ids["project"], scope="project", limit_cost=100))
        db.commit()
    m = group(client, root, profiles)
    calls = []
    original = MockProvider.submit

    def submit(self, req):
        model = self._cfg["config"]["model"]
        calls.append(model)
        if model == "one":
            raise SubmissionRejected("上游限流，未创建任务")
        return original(self, req)

    monkeypatch.setattr(MockProvider, "submit", submit)
    result = generate(client, root, ids, m)
    assert result["status"] == "succeeded", result
    assert calls == ["one", "two"]
    assert result["estimated_cost"] == 7 and result["actual_cost"] == 3
    assert [a["phase"] for a in result["routing_state"]["attempts"]] == [
        "dispatching",
        "rejected",
        "dispatching",
        "accepted",
        "generated",
    ]
    assert "test-secret" not in json.dumps(result)
    with sessions() as db:
        assert db.scalar(select(Quota.used_cost)) == Decimal("3.00")
    detail = client.get(root + f"/jobs/{result['id']}/workspace").json()
    assert detail["summary"]["model_name"] == m["name"]
    assert detail["outputs"][0]["model"] == "two"
    assert (
        detail["outputs"][0]["input_refs"]["routing"]["provider_name"]
        == profiles[1]["provider_name"]
    )


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), ProviderError("HTTP 500")])
def test_uncertain_submit_never_fails_over_or_retries(studio, monkeypatch, failure):
    client, _, ids = studio
    _, root, profiles = pair(studio)
    m = group(client, root, profiles)
    calls = []

    def fail(self, req):
        calls.append(self._cfg["config"]["model"])
        raise failure

    monkeypatch.setattr(MockProvider, "submit", fail)
    result = generate(client, root, ids, m)
    assert result["status"] == "failed"
    assert calls == ["one"]
    assert result["routing_state"]["phase"] == "submission_unknown"
    assert client.post(root + f"/jobs/{result['id']}/retry").status_code == 409
    assert not client.get(root + f"/jobs/{result['id']}/workspace").json()["can_retry"]


def test_poll_failure_does_not_switch_or_recreate_sync_generation(studio, monkeypatch):
    client, _, ids = studio
    _, root, profiles = pair(studio)
    m = group(client, root, profiles)
    original, calls = MockProvider.submit, []

    def submit(self, req):
        calls.append(self._cfg["config"]["model"])
        return original(self, req)

    def poll(self, handle):
        raise ProviderError("download disconnected")

    monkeypatch.setattr(MockProvider, "submit", submit)
    monkeypatch.setattr(MockProvider, "poll", poll)
    result = generate(client, root, ids, m)
    assert calls == ["one"] and result["status"] == "failed"
    assert result["routing_state"]["phase"] == "accepted"
    assert client.post(root + f"/jobs/{result['id']}/retry").status_code == 409


def test_disable_and_changed_profile_are_checked_before_network(studio, monkeypatch):
    client, sessions, ids = studio
    _, root, profiles = pair(studio)
    m = group(client, root, profiles)
    queued = []
    original_dispatch = jobs.dispatch

    def defer(db, job):
        queued.append(job.id)
        db.commit()
        return job

    monkeypatch.setattr(jobs, "dispatch", defer)
    result = generate(client, root, ids, m)
    assert result["status"] == "pending"
    with sessions() as db:
        p = db.get(ProviderConfig, uuid.UUID(profiles[0]["id"]))
        p.config = {**p.config, "model": "changed", "revision": 2}
        db.commit()
        jobs.execute(db, queued[0])
    result = client.get(root + f"/jobs/{result['id']}").json()
    assert result["status"] == "succeeded", result
    assert result["routing_state"]["provider_name"] == profiles[1]["provider_name"]
    assert result["routing_state"]["attempts"][0]["phase"] == "skipped"
    # The stored task definition was not overwritten by edits to the live profile.
    assert result["input_snapshot"]["routing"]["candidates"][0]["model_profile"]["model"] == "one"
    monkeypatch.setattr(jobs, "dispatch", original_dispatch)


def test_default_text_routes_and_channel_disable(studio):
    client, sessions, ids = studio
    root, first_channel = setup(client, ids["project"])
    _, second_channel = setup(client, ids["project"], name="backup")
    profiles = [
        add(client, root, ch, protocol="cloud_llm", modality="text", model=model)
        for ch, model in [(first_channel, "text-a"), (second_channel, "text-b")]
    ]
    model = group(client, root, profiles, modality="text", is_default=True)
    assert (
        client.post(
            root + f"/creative-models/{model['id']}/simulate", json={"prompt": "x"}
        ).status_code
        == 422
    )
    from app.modules.generation.channels import llm_config

    with sessions() as db:
        assert llm_config(db, ids["project"]).config["model"] == "text-a"
        db.get(ModelChannel, uuid.UUID(first_channel)).enabled = False
        db.commit()
        assert llm_config(db, ids["project"]).config["model"] == "text-b"


@pytest.mark.parametrize("protocol", ["openai_video", "video_v1", "ark_video", "minimax_video"])
def test_real_video_protocol_rejection_then_resume_without_second_post(
    studio, monkeypatch, tmp_path, protocol
):
    from app.modules.generation import media_engine

    client, sessions, ids = studio
    root, a = setup(client, ids["project"], endpoint="https://one.example/v1")
    _, b = setup(client, ids["project"], endpoint="https://two.example/v1")
    profiles = [
        add(
            client,
            root,
            ch,
            protocol=protocol,
            modality="video",
            model="video",
            parameters={"duration": [6]},
            price={"mode": "per_second", "amount": "2"},
        )
        for ch in [a, b]
    ]
    m = group(client, root, profiles, modality="video")
    video = tmp_path / "video.mp4"
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=128x128:r=5:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ]
    )
    posts, downloads = [], []

    def handler(req):
        if req.method == "POST":
            posts.append(req.url.host)
            if req.url.host == "one.example":
                return httpx.Response(429)
            return httpx.Response(
                200,
                json={
                    "id": "original-video",
                    "task_id": "original-video",
                    "base_resp": {"status_code": 0},
                },
            )
        if req.url.path.endswith("/content"):
            downloads.append(1)
            return (
                httpx.Response(503)
                if len(downloads) == 1
                else httpx.Response(200, content=video.read_bytes())
            )
        if protocol == "ark_video":
            return httpx.Response(
                200,
                json={
                    "status": "succeeded",
                    "content": {"video_url": "https://cdn.example/video.mp4"},
                },
            )
        if protocol == "minimax_video":
            if req.url.path.endswith("/files/retrieve"):
                return httpx.Response(
                    200,
                    json={
                        "base_resp": {"status_code": 0},
                        "file": {"download_url": "https://cdn.example/video.mp4"},
                    },
                )
            return httpx.Response(
                200,
                json={"status": "Success", "file_id": "file-1", "base_resp": {"status_code": 0}},
            )
        return httpx.Response(200, json={"status": "completed"})

    if protocol not in ("openai_video", "video_v1"):
        import io
        import urllib.request

        def download(url, **_):
            assert url == "https://cdn.example/video.mp4"
            downloads.append(1)
            if len(downloads) == 1:
                raise OSError("download interrupted")
            return io.BytesIO(video.read_bytes())

        monkeypatch.setattr(urllib.request, "urlopen", download)
    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(**kw, transport=httpx.MockTransport(handler))
    )
    result = generate(client, root, ids, m, request_type="video", provider_params={"duration": 6})
    assert result["status"] == "failed", result
    assert posts == ["one.example", "two.example"]
    # A changed endpoint prevents recovery; retrying that failure must still resume
    # the original handle rather than falling back to a new submission.
    with sessions() as db:
        db.get(ModelChannel, uuid.UUID(b)).endpoint = "https://changed.example/v1"
        db.commit()
    retry = client.post(root + f"/jobs/{result['id']}/retry")
    assert retry.status_code == 200, retry.text
    interrupted = client.get(root + f"/jobs/{retry.json()['id']}").json()
    assert interrupted["status"] == "failed"
    assert interrupted["routing_state"]["resume"]["external_job_id"] == "original-video"
    with sessions() as db:
        db.get(ModelChannel, uuid.UUID(b)).endpoint = "https://two.example/v1"
        db.commit()
    retry = client.post(root + f"/jobs/{interrupted['id']}/retry")
    assert retry.status_code == 200, retry.text
    recovered = client.get(root + f"/jobs/{retry.json()['id']}").json()
    assert recovered["status"] == "succeeded", recovered
    assert posts == ["one.example", "two.example"]  # Poll/download only, no regeneration.
    assert recovered["actual_cost"] == 12


def test_direct_channel_profile_uses_configured_price(studio):
    client, _, ids = studio
    _, root, profiles = pair(studio, price={"mode": "per_request", "amount": "4.5"})
    job = generate(client, root, ids, profiles[0])
    assert job["status"] == "succeeded", job
    assert job["estimated_cost"] == job["actual_cost"] == 4.5


def test_quota_reserves_maximum_candidate_price(studio):
    client, sessions, ids = studio
    _, root, profiles = pair(studio, price={"mode": "per_request", "amount": "8"})
    m = group(client, root, profiles)
    with sessions() as db:
        db.add(Quota(project_id=ids["project"], scope="project", limit_cost=5))
        db.commit()
    response = client.post(
        root + f"/shots/{ids['shot']}/generate", json={"provider": m["provider_name"]}
    )
    assert response.status_code == 402
    with sessions() as db:
        assert not db.scalar(select(GenerationJob.id))
