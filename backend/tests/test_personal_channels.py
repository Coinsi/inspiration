"""Exercise the personal provider workspace through its real API and job entry points."""

# ruff: noqa: F811
import uuid

from app.adapters.contracts import GenerationResult, JobHandle, Output
from app.adapters.generation.gpt_image import GptImageProvider
from app.core.security import create_access_token
from app.models.identity import User
from app.modules.generation import channels
from app.modules.generation.media_engine import mock_image
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import add, setup
from tests.test_personal_models import connect

ROOT = "/api/v1/me/model-channels"


def create_connection(client):
    data = {
        "request_id": str(uuid.uuid4()),
        "name": "个人图像服务",
        "provider_template_id": "openai",
        "endpoint": "https://api.openai.com/v1",
        "token": "private-test-credential",
    }
    response = client.post(ROOT, json=data)
    assert response.status_code == 200, response.text
    assert response.json() == client.post(ROOT, json=data).json()
    return f"{ROOT}/{response.json()['id']}"


def test_personal_workspace_routes_exact_model_and_preserves_private_credentials(
    studio, monkeypatch
):
    client, sessions, ids = studio
    path = create_connection(client)
    profile = {
        "request_id": str(uuid.uuid4()),
        "name": "我的图片模型",
        "model": "personal-image-test",
        "modality": "image",
        "protocol": "gpt_image",
    }
    created = client.post(path + "/models", json=profile)
    assert created.status_code == 200, created.text
    original = created.json()
    assert client.post(path + "/models", json=profile).json() == original
    name = original["provider_name"]
    assert name.startswith("personal_") and len(name) <= 64
    catalog = client.get(ROOT)
    assert "private-test-credential" not in catalog.text
    assert catalog.json()["channels"][0]["models"][0]["verification"]["status"] == "unverified"
    project = f"/api/v1/projects/{ids['project']}"
    assert any(p["provider_name"] == name for p in client.get(project + "/providers").json())
    assert client.get(project + "/model-channels").json()["channels"] == []

    seen = []

    def submit(adapter, req):
        seen.append((adapter.endpoint, adapter.token, adapter.config["model"]))
        return JobHandle(external_job_id="test-image")

    monkeypatch.setattr(GptImageProvider, "submit", submit)
    monkeypatch.setattr(
        GptImageProvider,
        "poll",
        lambda *_: GenerationResult(
            status="succeeded", outputs=[Output(data=mock_image(), type="image")]
        ),
    )
    generated = client.post(
        project + "/workbench/generate",
        json={
            "request_key": str(uuid.uuid4()),
            "provider": name,
            "request_type": "image",
            "prompt_override": "窗边的一盆绿植",
        },
    )
    assert generated.status_code == 200 and generated.json()["status"] == "succeeded", (
        generated.text
    )
    assert seen == [("https://api.openai.com/v1", "private-test-credential", profile["model"])]
    assert generated.json()["estimated_cost"] == 0
    verified = client.get(ROOT).json()["channels"][0]["models"][0]
    assert verified["verification"]["status"] == "verified"
    edit_path = path + "/models/" + original["id"]
    unchanged = client.put(edit_path, json={**profile, "revision": verified["revision"]})
    assert unchanged.json() == verified
    edited = client.put(
        edit_path, json={**profile, "revision": verified["revision"], "model": "v2"}
    )
    assert edited.json()["verification"]["status"] == "unverified"
    assert edited.json()["provider_name"] == name
    assert (
        client.put(edit_path, json={**profile, "revision": verified["revision"]}).status_code == 409
    )

    with sessions() as db:
        other = User(
            username="stranger",
            email="stranger@example.com",
            display_name="Other",
            password_hash="unused",
        )
        db.add(other)
        db.flush()
        token = create_access_token(str(other.id))
        db.commit()
    client.headers["Authorization"] = "Bearer " + token
    assert client.get(ROOT).json()["channels"] == []
    assert client.post(path + "/discover").status_code == 404
    assert client.post(path + "/models", json=profile).status_code == 404
    assert client.delete(path + "?revision=1").status_code == 404


def test_older_presets_keep_model_identity_and_use_personal_key_for_discovery(studio, monkeypatch):
    client, _, _ = studio
    existing = connect(client)
    catalog = client.get(ROOT).json()
    ch = catalog["channels"][0]
    assert ch["provider_template_id"] == "ark" and ch["id"] == existing["id"]
    assert "ark" in catalog["suggestions"]
    assert ch["models"][0]["provider_name"].endswith("_seedream-4.5")
    from app.core.crypto import decrypt

    requests = []

    def discover(connection, protocol, *, purpose=None):
        requests.append((connection.endpoint, decrypt(connection.credentials_encrypted), purpose))
        return {"models": ["example"]}

    monkeypatch.setattr(channels, "discover_connection", discover)
    path = ROOT + "/" + ch["id"]
    assert client.post(path + "/discover?purpose=image").json()["models"] == ["example"]
    assert requests == [(ch["endpoint"], "test-private-key", "image")]
    conn = {"name": ch["name"], "endpoint": ch["endpoint"], "revision": ch["revision"]}
    assert client.put(path, json=conn).status_code == 200
    assert client.get(ROOT).json()["channels"][0]["revision"] == ch["revision"]
    assert (
        client.post(
            ROOT + "/check?channel_id=" + ch["id"],
            json={**conn, "endpoint": "https://new.example/v1"},
        ).status_code
        == 422
    )
    assert len(requests) == 1
    assert (
        client.post(
            ROOT, json={"name": "Local", "endpoint": "https://127.0.0.1", "token": "fixture"}
        ).status_code
        == 422
    )


def test_imported_custom_model_can_create_job_under_legacy_storage_limit(studio, monkeypatch):
    client, _, ids = studio
    project, cid = setup(client, ids["project"])
    add(client, project, cid)
    imported = client.post(project + f"/model-channels/{cid}/use-personally", json={}).json()
    ch = client.get(ROOT).json()["channels"][0]
    model = ch["models"][0]
    assert ch["id"] == imported["id"] and len(model["id"]) == 32
    assert model["id"] == imported["models"][0]["id"]
    assert len(model["provider_name"]) <= 64
    monkeypatch.setattr(GptImageProvider, "submit", lambda *_: JobHandle(external_job_id="fixture"))
    monkeypatch.setattr(
        GptImageProvider,
        "poll",
        lambda *_: GenerationResult(
            status="succeeded", outputs=[Output(data=mock_image(), type="image")]
        ),
    )
    result = client.post(
        project + "/workbench/generate",
        json={
            "request_key": str(uuid.uuid4()),
            "provider": model["provider_name"],
            "request_type": "image",
            "prompt_override": "窗边的一盆绿植",
        },
    )
    assert result.status_code == 200 and result.json()["status"] == "succeeded", result.text
