"""Account and project membership regressions against isolated PostgreSQL schemas."""

# ruff: noqa: F811
import io

import pytest
from PIL import Image
from sqlalchemy.exc import IntegrityError

from app.core.errors import Unauthorized
from app.core.security import create_access_token, decode_access_token, hash_password
from app.models.identity import User, UserIdentity
from app.modules.identity.external import VerifiedIdentity, login_linked_identity
from tests.test_media_workflow import studio  # noqa: F401


def test_registration_profile_and_duplicate_email(studio):
    client, _, _ = studio
    payload = dict(
        username="new_writer",
        display_name=" 新编剧 ",
        email="Writer@example.com",
        password="safe-pass123",
    )
    result = client.post("/api/v1/auth/register", json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["display_name"] == "新编剧"
    assert "password_hash" not in result.json()
    assert (
        client.post(
            "/api/v1/auth/register",
            json={**payload, "username": "other_writer", "email": "writer@example.com"},
        ).status_code
        == 409
    )
    assert (
        client.post("/api/v1/auth/register", json={**payload, "password": "short"}).status_code
        == 422
    )
    assert (
        client.post("/api/v1/auth/register", json={**payload, "password": "字" * 25}).status_code
        == 422
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"username": payload["username"], "password": payload["password"]},
    )
    client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
    assert client.get("/api/v1/me").json()["memberships"] == []
    assert client.put("/api/v1/me/profile", json={"display_name": "   "}).status_code == 422
    updated = client.put("/api/v1/me/profile", json={"display_name": "小林", "bio": "写故事"})
    assert updated.status_code == 200
    assert client.get("/api/v1/me").json()["user"]["bio"] == "写故事"


def test_avatar_validation_and_removal(studio):
    client, _, _ = studio
    bad = client.post("/api/v1/me/avatar", files={"file": ("fake.png", b"<svg/>", "image/png")})
    assert bad.status_code == 422
    large = client.post(
        "/api/v1/me/avatar",
        files={"file": ("large.png", b"x" * (5 * 1024 * 1024 + 1), "image/png")},
    )
    assert large.status_code == 422
    output = io.BytesIO()
    Image.new("RGB", (400, 200), "blue").save(output, "PNG")
    uploaded = client.post(
        "/api/v1/me/avatar", files={"file": ("image.png", output.getvalue(), "image/png")}
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["avatar_data"].startswith("data:image/webp;base64,")
    assert client.delete("/api/v1/me/avatar").json()["avatar_data"] is None


def test_password_change_invalidates_all_old_sessions_including_media(studio):
    client, sessions, ids = studio
    with sessions() as db:
        db.get(User, ids["user"]).password_hash = hash_password("old-pass123")
        db.commit()
    old = client.headers["Authorization"]
    other = create_access_token(str(ids["user"]))
    assert (
        client.post(
            "/api/v1/me/password", json={"current_password": "wrong", "new_password": "new-pass123"}
        ).status_code
        == 422
    )
    assert client.get("/api/v1/me").status_code == 200
    changed = client.post(
        "/api/v1/me/password",
        json={"current_password": "old-pass123", "new_password": "new-pass123"},
    )
    assert changed.status_code == 200, changed.text
    for token in (old.removeprefix("Bearer "), other):
        assert (
            client.get("/api/v1/me", headers={"Authorization": "Bearer " + token}).status_code
            == 401
        )
        assert (
            client.get(
                f"/api/v1/projects/{ids['project']}/blobs/missing", params={"token": token}
            ).status_code
            == 401
        )
    client.headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    assert client.get("/api/v1/me").status_code == 200
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "media_test", "password": "old-pass123"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "media_test", "password": "new-pass123"}
        ).status_code
        == 200
    )


def test_membership_management_and_owner_protection(studio):
    client, sessions, ids = studio
    with sessions() as db:
        guest = User(
            username="guest_writer",
            email="guest@example.com",
            display_name="Guest",
            password_hash="unused",
        )
        db.add(guest)
        db.flush()
        guest_id = guest.id
        db.commit()
    base = f"/api/v1/projects/{ids['project']}/members"
    owner = client.headers["Authorization"]
    added = client.post(base, json={"username": "guest_writer", "role": "writer"})
    assert added.status_code == 200, added.text
    assert client.post(base, json={"username": "guest_writer", "role": "admin"}).status_code == 409
    rows = client.get(base).json()
    assert any(r["username"] == "guest_writer" and r["display_name"] == "Guest" for r in rows)
    assert all("email" not in r for r in rows)
    assert client.patch(base + f"/{ids['user']}", json={"role": "viewer"}).status_code == 403
    assert client.delete(base + f"/{ids['user']}").status_code == 403
    assert (
        client.post(base, json={"user_id": str(ids["user"]), "role": "viewer"}).status_code == 403
    )
    client.headers["Authorization"] = "Bearer " + create_access_token(str(guest_id))
    assert client.patch(base + f"/{guest_id}", json={"role": "admin"}).status_code == 403
    assert client.delete(base + f"/{ids['user']}").status_code == 403
    client.headers["Authorization"] = owner
    assert client.patch(base + f"/{guest_id}", json={"role": "admin"}).status_code == 200
    assert client.delete(base + f"/{guest_id}").status_code == 204
    client.headers["Authorization"] = "Bearer " + create_access_token(str(guest_id))
    assert client.get(base).status_code == 403
    assert client.get(f"/api/v1/projects/{ids['other']}/members").status_code == 403


def test_external_identity_contract_never_falls_back_to_email(studio):
    client, sessions, ids = studio
    assert client.get("/api/v1/auth/providers").json()["providers"] == []
    with sessions() as db:
        identity = UserIdentity(
            user_id=ids["user"],
            provider="oidc_test",
            issuer="https://issuer.example",
            subject="unique-subject",
        )
        db.add(identity)
        db.commit()
        token = login_linked_identity(
            db, VerifiedIdentity("oidc_test", "https://issuer.example", "unique-subject")
        )
        assert decode_access_token(token)["sub"] == str(ids["user"])
        for identity in (
            VerifiedIdentity("oidc_test", "https://another.example", "unique-subject"),
            VerifiedIdentity("oidc_test", "https://issuer.example", "media@example.com"),
        ):
            with pytest.raises(Unauthorized):
                login_linked_identity(db, identity)
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(
                    UserIdentity(
                        user_id=ids["user"],
                        provider="oidc_test",
                        issuer="https://issuer.example",
                        subject="unique-subject",
                    )
                )
                db.flush()
        db.get(User, ids["user"]).is_active = False
        db.flush()
        with pytest.raises(Unauthorized):
            login_linked_identity(
                db, VerifiedIdentity("oidc_test", "https://issuer.example", "unique-subject")
            )
