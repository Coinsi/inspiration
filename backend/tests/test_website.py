"""Website release isolation and public media authorization."""

# ruff: noqa: F811
import io
import uuid

from PIL import Image

from app.models.identity import User
from tests.test_media_workflow import studio  # noqa: F401


def administrator(studio):
    client, sessions, ids = studio
    with sessions() as db:
        db.get(User, ids["user"]).is_platform_admin = True
        db.commit()
    return client


def save(client, content, version):
    result = client.put(
        "/api/v1/admin/website/draft", json={"version": version, "content": content}
    )
    assert result.status_code == 200, result.text
    return result.json()


def publish(client, version):
    result = client.post(
        "/api/v1/admin/website/publish", json={"version": version, "note": "Test release"}
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_project_admin_cannot_manage_website_or_escalate(studio):
    client, _, _ = studio
    assert client.get("/api/v1/website").status_code == 200
    assert client.get("/api/v1/admin/website").status_code == 403
    assert (
        client.put(
            "/api/v1/admin/website/draft",
            json={"version": 1, "content": client.get("/api/v1/website").json()["content"]},
        ).status_code
        == 403
    )
    assert client.post("/api/v1/admin/website/publish", json={"version": 1}).status_code == 403
    assert client.get("/api/v1/admin/website/history").status_code == 403
    assert client.get("/api/v1/admin/website/media").status_code == 403
    assert (
        client.post(
            "/api/v1/admin/website/media", files={"file": ("fake.png", b"fake", "image/png")}
        ).status_code
        == 403
    )
    result = client.post(
        "/api/v1/auth/register",
        json={
            "username": "site_guest",
            "email": "site@example.com",
            "password": "password123",
            "display_name": "Guest",
            "is_platform_admin": True,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["is_platform_admin"] is False
    client.headers.pop("Authorization")
    assert client.get("/api/v1/admin/website").status_code == 401
    assert client.get("/api/v1/website").status_code == 200


def test_draft_publish_restore_and_conflicting_versions(studio):
    client = administrator(studio)
    initial = client.get("/api/v1/website").json()["content"]
    state = client.get("/api/v1/admin/website").json()
    draft = state["draft"]
    draft["title"] = "尚未发布的标题"
    state = save(client, draft, state["version"])
    assert client.get("/api/v1/website").json()["content"] == initial
    assert client.post("/api/v1/admin/website/publish", json={"version": 1}).status_code == 409
    first = publish(client, state["version"])
    assert first["published_revision"] == 1
    assert client.get("/api/v1/website").json()["content"]["title"] == "尚未发布的标题"
    draft["title"] = "第二次发布"
    second = publish(client, save(client, draft, first["version"])["version"])
    restored = client.post(
        "/api/v1/admin/website/history/1/restore", json={"version": second["version"]}
    )
    assert restored.status_code == 200
    assert restored.json()["draft"]["title"] == "尚未发布的标题"
    assert client.get("/api/v1/website").json()["content"]["title"] == "第二次发布"
    assert (
        client.put(
            "/api/v1/admin/website/draft", json={"version": second["version"], "content": draft}
        ).status_code
        == 409
    )
    publish(client, restored.json()["version"])
    assert client.get("/api/v1/website").json()["content"]["title"] == "尚未发布的标题"
    history = client.get("/api/v1/admin/website/history").json()
    assert [r["revision"] for r in history] == [3, 2, 1]


def test_explicit_media_publication_and_withdrawal(studio):
    client = administrator(studio)
    image = io.BytesIO()
    Image.new("RGB", (24, 24), "green").save(image, "PNG")
    result = client.post(
        "/api/v1/admin/website/media",
        files={"file": ("website.png", image.getvalue(), "image/png")},
    )
    assert result.status_code == 200, result.text
    media = result.json()["id"]
    url = f"/api/v1/website/media/{media}"
    assert client.get(url).status_code == 404
    assert client.get(f"/api/v1/admin/website/media/{media}").content == image.getvalue()
    state = client.get("/api/v1/admin/website").json()
    draft = state["draft"]
    draft["hero_media_id"] = media
    state = save(client, draft, state["version"])
    assert client.get(url).status_code == 404
    state = publish(client, state["version"])
    token = client.headers.pop("Authorization")
    assert client.get(url).content == image.getvalue()
    partial = client.get(url, headers={"Range": "bytes=0-7"})
    assert partial.status_code == 206 and partial.content == image.getvalue()[:8]
    assert client.get(url, headers={"Range": "bytes=-0"}).status_code == 416
    assert client.get(url).headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/api/v1/admin/website/media/{media}").status_code == 401
    client.headers["Authorization"] = token
    draft["hero_media_id"] = None
    draft["features"][0]["media_id"] = media
    draft["sections"] = [
        {**s, "enabled": False} if s["key"] == "features" else s for s in draft["sections"]
    ]
    publish(client, save(client, draft, state["version"])["version"])
    assert client.get(url).status_code == 404


def test_content_and_media_validation(studio):
    client = administrator(studio)
    state = client.get("/api/v1/admin/website").json()
    content = state["draft"]
    content["hero_media_id"] = str(uuid.uuid4())
    assert (
        client.put(
            "/api/v1/admin/website/draft", json={"version": state["version"], "content": content}
        ).status_code
        == 422
    )
    content["hero_media_id"] = "https://private.invalid/file"
    assert (
        client.put(
            "/api/v1/admin/website/draft", json={"version": state["version"], "content": content}
        ).status_code
        == 422
    )
    content["hero_media_id"] = None
    content["sections"][1] = content["sections"][0]
    assert (
        client.put(
            "/api/v1/admin/website/draft", json={"version": state["version"], "content": content}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/admin/website/media",
            files={"file": ("fake.png", b'<svg onload="alert(1)"/>', "image/png")},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/admin/website/history/999/restore", json={"version": state["version"]}
        ).status_code
        == 404
    )


def test_revoking_platform_access_takes_effect_for_existing_token(studio):
    client = administrator(studio)
    assert client.get("/api/v1/admin/website").status_code == 200
    _, sessions, ids = studio
    with sessions() as db:
        db.get(User, ids["user"]).is_platform_admin = False
        db.commit()
    assert client.get("/api/v1/admin/website").status_code == 403
    assert client.get("/api/v1/me").json()["user"]["is_platform_admin"] is False
