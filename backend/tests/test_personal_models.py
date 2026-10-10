"""Account service isolation, cross-project reuse, durable verification and error diagnostics."""

# ruff: noqa: F811
import uuid

import httpx
import pytest

from app.adapters.contracts import GenerationResult, JobHandle, Output
from app.adapters.generation.native_media import ArkImageProvider
from app.core.security import create_access_token
from app.models.identity import Membership, User
from app.modules.generation.media_engine import mock_image
from app.modules.generation.model_routing import SubmissionRejected
from tests.test_media_workflow import studio  # noqa: F401


def connect(client):
    response = client.put(
        "/api/v1/me/model-services/ark",
        json={"preset_id": "ark", "token": "test-private-key", "models": ["seedream-4.5"]},
    )
    assert response.status_code == 200, response.text
    assert "test-private-key" not in response.text
    return response.json()


def test_personal_models_cross_projects_and_cannot_be_used_by_members(studio):
    client, sessions, ids = studio
    connection = connect(client)
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="editor"))
        stranger = User(
            username="other-member",
            email="other@example.com",
            password_hash="unused",
            display_name="Other",
        )
        db.add(stranger)
        db.flush()
        stranger_id = stranger.id
        db.add(Membership(project_id=uuid.UUID(ids["project"]), user_id=stranger_id, role="admin"))
        db.commit()
    root = f"/api/v1/projects/{ids['project']}"
    listed = client.get(root + "/providers").json()
    name = next(p["provider_name"] for p in listed if p["provider_name"].startswith("personal_"))
    assert any(
        p["provider_name"] == name
        for p in client.get(f"/api/v1/projects/{ids['other']}/providers").json()
    )
    assert connection["models"][0]["verification"]["status"] == "unverified"
    client.headers["Authorization"] = "Bearer " + create_access_token(str(stranger_id))
    assert client.get("/api/v1/me/model-services").json() == []
    assert not any(p["provider_name"] == name for p in client.get(root + "/providers").json())
    assert client.get(root + f"/providers/{name}/capabilities").status_code == 404
    for endpoint in ["estimate", "generate"]:
        assert (
            client.post(
                root + "/workbench/" + endpoint,
                json={
                    "request_key": str(uuid.uuid4()),
                    "provider": name,
                    "request_type": "image",
                    "prompt_override": "lake",
                },
            ).status_code
            == 404
        )


def test_generation_verification_and_key_update(studio, monkeypatch):
    client, sessions, ids = studio
    c = connect(client)
    monkeypatch.setattr(
        ArkImageProvider, "submit", lambda *_: JobHandle(external_job_id="test-image")
    )
    monkeypatch.setattr(
        ArkImageProvider,
        "poll",
        lambda *_: GenerationResult(
            status="succeeded", outputs=[Output(data=mock_image(), type="image")]
        ),
    )
    name = "personal_" + uuid.UUID(c["id"]).hex + "_seedream-4.5"
    root = f"/api/v1/projects/{ids['project']}"
    r = client.post(
        root + "/workbench/generate",
        json={
            "request_key": str(uuid.uuid4()),
            "provider": name,
            "request_type": "image",
            "prompt_override": "lake",
            "provider_params": {"size": "2K"},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded", r.text
    assert (
        client.get("/api/v1/me/model-services").json()[0]["models"][0]["verification"]["status"]
        == "verified"
    )
    assert len(client.get(root + "/workbench/creations").json()[0]["outputs"]) == 1
    # Selecting another model does not require the key again.
    r = client.put(
        "/api/v1/me/model-services/ark",
        json={"preset_id": "ark", "revision": 1, "models": ["seedream-4.5", "seedream-5.0-lite"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["models"][0]["verification"]["status"] == "verified"
    assert r.json()["models"][1]["verification"]["status"] == "unverified"
    assert (
        client.put(
            "/api/v1/me/model-services/ark",
            json={"preset_id": "ark", "revision": 1, "models": ["seedream-4.5"]},
        ).status_code
        == 409
    )
    r = client.put(
        "/api/v1/me/model-services/ark",
        json={
            "preset_id": "ark",
            "revision": 2,
            "token": "rotated-secret",
            "models": ["seedream-4.5"],
        },
    )
    assert r.json()["models"][0]["verification"]["status"] == "unverified"


def test_rejection_records_specific_failure(studio, monkeypatch):
    client, sessions, ids = studio
    c = connect(client)

    def fail(*_):
        raise SubmissionRejected("ModelNotOpen: enable model")

    monkeypatch.setattr(ArkImageProvider, "submit", fail)
    r = client.post(
        f"/api/v1/projects/{ids['project']}/workbench/generate",
        json={
            "request_key": str(uuid.uuid4()),
            "provider": "personal_" + uuid.UUID(c["id"]).hex + "_seedream-4.5",
            "request_type": "image",
            "prompt_override": "lake",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed"
    v = client.get("/api/v1/me/model-services").json()[0]["models"][0]["verification"]
    assert v["status"] == "failed" and "ModelNotOpen" in v["message"]


def test_native_error_code_without_echoing_secrets(monkeypatch):
    original = httpx.Client
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            404,
            json={
                "error": {
                    "code": "ModelNotOpen",
                    "message": "token=secret-sensitive prompt=private",
                }
            },
        )
    )
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=transport, **kw))
    provider = ArkImageProvider(endpoint="https://ark.example/api/v3", token="sensitive")
    with pytest.raises(SubmissionRejected) as caught:
        provider._request("POST", "/images/generations", json={})
    assert "ModelNotOpen" in str(caught.value)
    assert "sensitive" not in str(caught.value) and "private" not in str(caught.value)


def test_reuse_existing_text_service_keeps_secret_private_and_does_not_overwrite(studio):
    from app.core.crypto import decrypt
    from app.modules.generation.channels import selected_llm
    from tests.test_model_channels import add, setup

    client, sessions, ids = studio
    root, channel = setup(client, ids["project"])
    add(client, root, channel, protocol="cloud_llm", modality="text", model="example-chat")
    r = client.post(root + f"/model-channels/{channel}/use-personally", json={})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert "credentials" not in r.text and "token" not in r.text
    assert saved["models"][0]["modality"] == "text"
    name = "personal_" + uuid.UUID(saved["id"]).hex + "_" + saved["models"][0]["id"]
    with sessions() as db:
        pc = selected_llm(db, uuid.UUID(ids["project"]), name, user_id=ids["user"])
        assert pc.config["model"] == "example-chat"
        assert decrypt(pc.credentials_encrypted)
        with pytest.raises(Exception, match="个人模型服务不存在"):
            selected_llm(db, uuid.UUID(ids["project"]), name, user_id=uuid.uuid4())
    # A second import returns the existing account connection, never silently rotates it.
    repeated = client.post(root + f"/model-channels/{channel}/use-personally", json={})
    assert repeated.json()["revision"] == saved["revision"]


def test_private_video_retry_resumes_without_new_submit(studio, monkeypatch):
    from app.adapters.generation.native_media import ArkVideoProvider

    client, sessions, ids = studio
    r = client.put(
        "/api/v1/me/model-services/ark",
        json={"preset_id": "ark", "token": "test-private-key", "models": ["seedance-1.5-pro"]},
    )
    saved = r.json()
    submits = []

    def submit(*_):
        submits.append(True)
        return JobHandle(external_job_id="original-video-task")

    def poll(*_):
        raise RuntimeError("temporary query failure")

    monkeypatch.setattr(ArkVideoProvider, "submit", submit)
    monkeypatch.setattr(ArkVideoProvider, "poll", poll)
    root = f"/api/v1/projects/{ids['project']}"
    name = "personal_" + uuid.UUID(saved["id"]).hex + "_seedance-1.5-pro"
    first = client.post(
        root + "/workbench/generate",
        json={
            "request_key": str(uuid.uuid4()),
            "provider": name,
            "request_type": "video",
            "prompt_override": "lake",
        },
    ).json()
    assert first["status"] == "failed"
    retry = client.post(root + f"/jobs/{first['id']}/retry", json={})
    assert retry.status_code == 200, retry.text
    assert len(submits) == 1
    assert retry.json()["status"] == "failed"


def test_save_unchanged_keeps_pending_job_revision(studio):
    client, _, _ = studio
    c = connect(client)
    r = client.put(
        "/api/v1/me/model-services/ark",
        json={"preset_id": "ark", "revision": c["revision"], "models": ["seedream-4.5"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == c["revision"]


def test_imported_catalog_can_reenable_deselected_model(studio):
    from tests.test_model_channels import add, setup

    client, _, ids = studio
    root, channel = setup(client, ids["project"])
    add(client, root, channel, protocol="cloud_llm", modality="text", model="chat-one")
    add(client, root, channel, protocol="cloud_llm", modality="text", model="chat-two")
    saved = client.post(root + f"/model-channels/{channel}/use-personally", json={}).json()
    keys = [m["id"] for m in saved["models"]]
    assert len(keys) == 2
    path = "/api/v1/me/model-services/" + saved["preset_id"]
    r = client.put(
        path,
        json={"preset_id": saved["preset_id"], "revision": saved["revision"], "models": keys[:1]},
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["models"]) == 1 and len(r.json()["catalog"]) == 2
    providers = client.get(root + "/providers").json()
    assert len([p for p in providers if p["provider_name"].startswith("personal_")]) == 1
    r = client.put(
        path,
        json={"preset_id": saved["preset_id"], "revision": r.json()["revision"], "models": keys},
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["models"]) == 2
