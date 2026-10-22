"""Account and project membership regressions against isolated PostgreSQL schemas."""

# ruff: noqa: F811
import io
import re

import pytest
from PIL import Image
from sqlalchemy import select
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


def test_minimal_registration_and_email_password_login(studio):
    client, sessions, _ = studio
    registered = client.post(
        "/api/v1/auth/register",
        json={"email": "  New.Writer@Example.COM  ", "password": "safe-pass123"},
    )
    assert registered.status_code == 200, registered.text
    user = registered.json()
    assert user["email"] == "new.writer@example.com"
    assert user["display_name"] == "新用户"
    assert re.fullmatch(r"u_[0-9a-f]{32}", user["username"])
    assert not user["is_platform_admin"]
    assert "password_hash" not in user
    with sessions() as db:
        stored = db.scalar(select(User).where(User.email == user["email"]))
        assert stored.password_hash and stored.password_hash != "safe-pass123"
    result = client.post(
        "/api/v1/auth/login",
        json={"email": "  NEW.WRITER@EXAMPLE.com  ", "password": "safe-pass123"},
    )
    assert result.status_code == 200, result.text
    assert decode_access_token(result.json()["access_token"])["sub"] == user["id"]
    client.headers["Authorization"] = "Bearer " + result.json()["access_token"]
    assert client.get("/api/v1/me").json()["memberships"] == []
    assert client.get("/api/v1/me").json()["user"]["username"] == user["username"]


def test_existing_username_account_can_login_by_email_without_changing_profile(studio):
    client, sessions, ids = studio
    with sessions() as db:
        user = db.get(User, ids["user"])
        user.email = " Legacy.Writer@Example.COM "
        user.password_hash = hash_password("safe-pass123")
        db.commit()
    for identifier in ({"email": "legacy.writer@example.com"}, {"username": "media_test"}):
        result = client.post(
            "/api/v1/auth/login", json={**identifier, "password": "safe-pass123"}
        )
        assert result.status_code == 200, result.text
        assert decode_access_token(result.json()["access_token"])["sub"] == str(ids["user"])
    with sessions() as db:
        user = db.get(User, ids["user"])
        assert user.username == "media_test"
        assert user.display_name == "Media Test"
        assert user.email == " Legacy.Writer@Example.COM "


@pytest.mark.parametrize("existing_email", ["writer@example.com", " Writer@Example.COM "])
def test_minimal_registration_rejects_existing_email_and_never_links_google(studio, existing_email):
    client, sessions, _ = studio
    with sessions() as db:
        existing = User(
            username="google_writer",
            email=existing_email,
            display_name="Google Writer",
            password_hash=None,
        )
        db.add(existing)
        db.flush()
        existing_id = existing.id
        db.add(
            UserIdentity(
                user_id=existing_id,
                provider="google",
                issuer="https://accounts.google.com",
                subject="google-writer-subject",
            )
        )
        db.commit()
    result = client.post(
        "/api/v1/auth/register",
        json={"email": "WRITER@example.com", "password": "safe-pass123"},
    )
    assert result.status_code == 409, result.text
    with sessions() as db:
        assert db.get(User, existing_id).password_hash is None
        assert len(list(db.scalars(select(User).where(User.username == "google_writer")))) == 1
        identity = db.scalar(select(UserIdentity).where(UserIdentity.user_id == existing_id))
        assert identity.subject == "google-writer-subject"


def test_email_login_failures_are_uniform_for_wrong_missing_inactive_and_google_only(studio):
    client, sessions, ids = studio
    with sessions() as db:
        db.get(User, ids["user"]).password_hash = hash_password("safe-pass123")
        db.add_all(
            [
                User(
                    username="disabled_writer",
                    email="disabled@example.com",
                    display_name="Disabled Writer",
                    password_hash=hash_password("safe-pass123"),
                    is_active=False,
                ),
                User(
                    username="google_only_writer",
                    email="google@example.com",
                    display_name="Google Writer",
                    password_hash=None,
                ),
            ]
        )
        db.commit()
    failures = []
    for email, password in (
        ("media@example.com", "wrong-password"),
        ("missing@example.com", "safe-pass123"),
        ("disabled@example.com", "safe-pass123"),
        ("google@example.com", "safe-pass123"),
    ):
        result = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert result.status_code == 401, result.text
        assert "access_token" not in result.json()
        failures.append(result.json())
    assert all(body == failures[0] for body in failures)


def test_email_login_rejects_ambiguous_legacy_emails_even_when_password_matches(studio):
    client, sessions, _ = studio
    with sessions() as db:
        password_hash = hash_password("safe-pass123")
        for username, email in (
            ("first_writer", "Writer@example.com"),
            ("second_writer", "writer@example.com"),
        ):
            db.add(
                User(
                    username=username,
                    email=email,
                    display_name=username,
                    password_hash=password_hash,
                )
            )
        db.commit()
    result = client.post(
        "/api/v1/auth/login", json={"email": "writer@example.com", "password": "safe-pass123"}
    )
    assert result.status_code == 401, result.text
    # Both existing accounts retain their explicit, unambiguous legacy login path.
    for username in ("first_writer", "second_writer"):
        result = client.post(
            "/api/v1/auth/login", json={"username": username, "password": "safe-pass123"}
        )
        assert result.status_code == 200, result.text


@pytest.mark.parametrize(
    "identifier",
    [{}, {"email": "not-an-email"}, {"email": "media@example.com", "username": "media_test"}],
)
def test_login_requires_one_valid_identifier(studio, identifier):
    client, _, _ = studio
    result = client.post("/api/v1/auth/login", json={**identifier, "password": "safe-pass123"})
    assert result.status_code == 422, result.text


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
