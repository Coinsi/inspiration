import hashlib
import uuid

from app.core.security import hash_password
from app.models.identity import User
from app.models.library import MediaVersion
from app.modules.library import service
from tests.test_media_workflow import (
    post_ok,
    studio,  # noqa: F401
)


def test_login_returns_same_authenticated_profile_without_another_round_trip(studio):
    client, sessions, ids = studio
    with sessions() as db:
        db.get(User, ids["user"]).password_hash = hash_password("valid-test-password")
        db.commit()
    result = client.post(
        "/api/v1/auth/login",
        json={
            "email": "media@example.com",
            "password": "valid-test-password",
        },
    )
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["token_type"] == "bearer"
    profile = client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {payload['access_token']}"}
    )
    assert payload["me"] == profile.json()
    assert "password_hash" not in payload["me"]["user"]
    assert (
        client.post(
            "/api/v1/auth/login",
            json={
                "email": "media@example.com",
                "password": "wrong",
            },
        ).status_code
        == 401
    )


def test_pending_upload_discovery_resume_and_project_isolation(studio, tmp_path, monkeypatch):
    client, sessions, ids = studio
    monkeypatch.setattr(service, "CHUNK_SIZE", 4096)
    monkeypatch.setattr(service.settings, "library_staging_dir", str(tmp_path / "staging"))
    base = f"/api/v1/projects/{ids['project']}/library"
    content = b"video-content-for-resume" * 400
    v = post_ok(
        client,
        f"{base}/uploads",
        {
            "filename": "resume.mp4",
            "size_bytes": len(content),
            "fingerprint": hashlib.sha256(content).hexdigest(),
        },
    )
    path = f"{base}/versions/{v['id']}"
    assert (
        client.post(f"{path}/chunk?offset=0", files={"file": ("chunk", content[:4096])}).status_code
        == 200
    )
    # A fresh list discovers the upload even though it has no ready/current version.
    pending = client.get(f"{base}/uploads").json()
    assert [p["id"] for p in pending] == [v["id"]]
    assert pending[0]["uploaded_bytes"] == 4096
    assert "chunk_hashes" not in pending[0]  # Hash manifest is only fetched for resume.
    assert client.get(f"/api/v1/projects/{ids['other']}/library/uploads").status_code == 403
    for offset in range(4096, len(content), 4096):
        assert (
            client.post(
                f"{path}/chunk?offset={offset}",
                files={"file": ("chunk", content[offset : offset + 4096])},
            ).status_code
            == 200
        )
    # An acknowledgement lost by the browser is reconciled from the server's offset.
    saved = client.get(path).json()
    assert saved["uploaded_bytes"] == len(content)
    assert len(saved["chunk_hashes"]) == 3
    assert service.staging_path(v["id"]).read_bytes() == content
    post_ok(client, f"{path}/complete")
    assert client.get(f"{base}/uploads").json() == []
    # Simulate processing completion before starting another version of the same media.
    with sessions() as db:
        db.get(MediaVersion, uuid.UUID(v["id"])).status = "ready"
        db.commit()
    later = post_ok(
        client,
        f"{base}/uploads",
        {
            "filename": "again.mp4",
            "size_bytes": len(content),
            "fingerprint": "b" * 64,
            "media_id": v["media_id"],
        },
    )
    assert client.get(f"{base}/uploads").json()[0]["id"] == later["id"]
    post_ok(client, f"{base}/versions/{later['id']}/cancel")
    assert client.get(f"{base}/uploads").json() == []
