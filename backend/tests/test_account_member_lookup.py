"""Username and private member lookup contracts in disposable PostgreSQL schemas."""

# ruff: noqa: F811
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event, select

from app.core.security import create_access_token, decode_access_token, hash_password
from app.models.identity import Membership, Project, User, UserIdentity
from tests.test_media_workflow import studio  # noqa: F401


def add_user(sessions, username="lookup_guest", email="guest@example.com", **extra):
    with sessions() as db:
        user = User(username=username, email=email, display_name="Guest", **extra)
        db.add(user)
        db.flush()
        user_id = user.id
        db.commit()
    return user_id


def membership(sessions, project_id, user_id, role="viewer"):
    with sessions() as db:
        row = Membership(project_id=uuid.UUID(str(project_id)), user_id=user_id, role=role)
        db.add(row)
        db.commit()


def members_url(ids):
    return f"/api/v1/projects/{ids['project']}/members"


def test_username_rename_keeps_uuid_sessions_login_identity_and_memberships(studio):
    client, sessions, ids = studio
    with sessions() as db:
        user = db.get(User, ids["user"])
        user.password_hash = hash_password("rename-pass123")
        user.avatar_data = "data:image/webp;base64,fixture"
        original_hash, original_version = user.password_hash, user.auth_version
        identity = UserIdentity(
            user_id=user.id,
            provider="google",
            issuer="https://accounts.google.com",
            subject="stable-google-subject",
        )
        db.add(identity)
        db.commit()
        identity_id = identity.id
    before = client.get("/api/v1/me").json()
    token = client.headers["Authorization"]
    changed = client.put(
        "/api/v1/me/profile",
        json={
            "username": "  Writer.New-42  ",
            "display_name": " 新名字 ",
            "bio": " 简介 ",
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["username"] == "Writer.New-42"
    assert changed.json()["display_name"] == "新名字"
    assert changed.json()["bio"] == "简介"
    for field in ("id", "email", "avatar_data", "is_active", "is_platform_admin"):
        assert changed.json()[field] == before["user"][field]
    after = client.get("/api/v1/me", headers={"Authorization": token})
    assert after.status_code == 200
    assert after.json()["memberships"] == before["memberships"]
    rows = client.get(members_url(ids)).json()
    owner = next(row for row in rows if row["user_id"] == str(ids["user"]))
    assert owner["username"] == "Writer.New-42" and owner["is_owner"]
    with sessions() as db:
        user = db.get(User, ids["user"])
        assert user.password_hash == original_hash and user.auth_version == original_version
        assert db.get(Project, uuid.UUID(ids["project"])).owner_id == ids["user"]
        assert db.get(UserIdentity, identity_id).user_id == ids["user"]
        assert db.get(UserIdentity, identity_id).subject == "stable-google-subject"
    for identifier in ({"username": "Writer.New-42"}, {"email": " MEDIA@EXAMPLE.COM "}):
        result = client.post(
            "/api/v1/auth/login", json={**identifier, "password": "rename-pass123"}
        )
        assert result.status_code == 200, result.text
        assert decode_access_token(result.json()["access_token"])["sub"] == str(ids["user"])
    assert (
        client.post(
            "/api/v1/auth/login",
            json={
                "username": "media_test",
                "password": "rename-pass123",
            },
        ).status_code
        == 401
    )


def test_legacy_profile_payload_and_same_username_remain_supported(studio):
    client, sessions, ids = studio
    for extra in ({}, {"username": None}, {"username": " media_test "}):
        response = client.put("/api/v1/me/profile", json={"display_name": "Updated", **extra})
        assert response.status_code == 200, response.text
        assert response.json()["username"] == "media_test"
    with sessions() as db:
        db.get(User, ids["user"]).username = "legacy name!"
        db.commit()
    legacy = client.put("/api/v1/me/profile", json={"display_name": "Legacy updated"})
    assert legacy.status_code == 200 and legacy.json()["username"] == "legacy name!"


@pytest.mark.parametrize("username", ["", "  ", "ab", "x" * 65, "not valid", "中文名", "mail@name"])
def test_invalid_username_cannot_partially_change_profile(studio, username):
    client, _, _ = studio
    original = client.get("/api/v1/me").json()["user"]
    response = client.put(
        "/api/v1/me/profile",
        json={
            "username": username,
            "display_name": "Must not persist",
            "bio": "Must not persist",
        },
    )
    assert response.status_code == 422, response.text
    assert client.get("/api/v1/me").json()["user"] == original


def test_duplicate_username_returns_conflict_without_changing_either_profile(studio):
    client, sessions, ids = studio
    target_id = add_user(sessions)
    original = client.get("/api/v1/me").json()["user"]
    response = client.put(
        "/api/v1/me/profile",
        json={
            "username": "lookup_guest",
            "display_name": "Must not persist",
            "bio": "Must not persist",
        },
    )
    assert response.status_code == 409, response.text
    assert client.get("/api/v1/me").json()["user"] == original
    with sessions() as db:
        assert db.get(User, target_id).display_name == "Guest"
        assert db.get(User, target_id).username == "lookup_guest"
        assert db.get(User, ids["user"]).username == "media_test"


def test_concurrent_username_claim_has_one_winner_and_atomic_conflict(studio):
    client, sessions, ids = studio
    other_id = add_user(sessions)
    barrier = threading.Barrier(2)
    bind = sessions.kw["bind"]

    def synchronize_updates(_conn, _cursor, statement, _parameters, _context, _many):
        # Align the real PostgreSQL writes after both availability checks. The
        # loser must be handled through the DB unique constraint, not a mocked error.
        if statement.lstrip().upper().startswith("UPDATE ") and "username=" in statement:
            barrier.wait(timeout=10)

    def rename(user_id):
        return client.put(
            "/api/v1/me/profile",
            headers={
                "Authorization": "Bearer " + create_access_token(str(user_id)),
            },
            json={"username": "same_candidate", "display_name": "Winner", "bio": "winner bio"},
        )

    event.listen(bind, "before_cursor_execute", synchronize_updates)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(rename, user_id) for user_id in (ids["user"], other_id)]
            responses = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(bind, "before_cursor_execute", synchronize_updates)
    assert sorted(response.status_code for response in responses) == [200, 409]
    with sessions() as db:
        users = [db.get(User, user_id) for user_id in (ids["user"], other_id)]
        assert sum(user.username == "same_candidate" for user in users) == 1
        loser = next(user for user in users if user.username != "same_candidate")
        assert loser.display_name == ("Media Test" if loser.id == ids["user"] else "Guest")
        assert loser.bio == ""


def test_lookup_normalizes_email_returns_only_preview_and_scopes_membership(studio):
    client, sessions, ids = studio
    target_id = add_user(
        sessions, email=" Guest@Example.COM ", avatar_data="data:image/webp;base64,fixture"
    )
    membership(sessions, ids["other"], target_id)
    base = members_url(ids)
    expected = {
        "user_id": str(target_id),
        "username": "lookup_guest",
        "display_name": "Guest",
        "avatar_data": "data:image/webp;base64,fixture",
        "is_member": False,
    }
    for selector in ({"email": " GUEST@example.com "}, {"username": " lookup_guest "}):
        response = client.post(base + "/lookup", json=selector)
        assert response.status_code == 200, response.text
        assert response.json() == expected
        assert (
            client.post(base + "/lookup", json={**selector, "member_only": True}).status_code == 404
        )
    # A full email is required; an exact prefix is a different address.
    assert client.post(base + "/lookup", json={"email": "gues@example.com"}).status_code == 404
    assert client.post(base + "/lookup", json={"username": "lookup_gues"}).status_code == 404
    added = client.post(
        base,
        json={
            "email": "GUEST@EXAMPLE.COM",
            "expected_user_id": str(target_id),
            "role": "writer",
        },
    )
    assert added.status_code == 200, added.text
    assert added.json()["user_id"] == str(target_id)
    response = client.post(
        base + "/lookup", json={"email": "guest@example.com", "member_only": True}
    )
    assert response.json() == {**expected, "is_member": True}
    assert all("email" not in row for row in client.get(base).json())
    assert "email" not in added.json()


@pytest.mark.parametrize("role", ["viewer", "writer", "artist", "director", None])
def test_nonadmins_and_other_project_admin_cannot_lookup_or_add(studio, role):
    client, sessions, ids = studio
    actor_id = add_user(sessions, username="limited_actor", email="actor@example.com")
    target_id = add_user(sessions)
    if role:
        membership(sessions, ids["project"], actor_id, role)
    membership(sessions, ids["other"], actor_id, "admin")
    client.headers["Authorization"] = "Bearer " + create_access_token(str(actor_id))
    base = members_url(ids)
    for member_only in (False, True):
        response = client.post(
            base + "/lookup", json={"email": "guest@example.com", "member_only": member_only}
        )
        assert response.status_code == 403, response.text
        assert "lookup_guest" not in response.text and "guest@example.com" not in response.text
    response = client.post(base, json={"email": "guest@example.com", "role": "writer"})
    assert response.status_code == 403, response.text
    with sessions() as db:
        assert db.scalar(select(Membership).where(Membership.user_id == target_id)) is None


def test_anonymous_lookup_and_profile_change_are_denied(studio):
    client, _, ids = studio
    client.headers.pop("Authorization")
    assert (
        client.post(members_url(ids) + "/lookup", json={"username": "media_test"}).status_code
        == 401
    )
    assert (
        client.put(
            "/api/v1/me/profile", json={"username": "new_name", "display_name": "New"}
        ).status_code
        == 401
    )


@pytest.mark.parametrize(
    "target",
    [
        {},
        {"username": "lookup_guest", "email": "guest@example.com"},
        {"username": "  "},
        {"email": "guest"},
        {"email": ""},
    ],
)
def test_lookup_and_add_require_one_well_formed_identifier(studio, target):
    client, _, ids = studio
    base = members_url(ids)
    assert client.post(base + "/lookup", json=target).status_code == 422
    assert client.post(base, json={**target, "role": "writer"}).status_code == 422


def test_ambiguous_legacy_email_never_resolves_using_active_status_or_membership(studio):
    client, sessions, ids = studio
    active_id = add_user(sessions, username="first_guest", email=" Guest@Example.COM ")
    add_user(sessions, username="second_guest", email="guest@example.com", is_active=False)
    membership(sessions, ids["project"], active_id)
    base = members_url(ids)
    for member_only in (False, True):
        response = client.post(
            base + "/lookup", json={"email": "GUEST@example.com", "member_only": member_only}
        )
        assert response.status_code == 404, response.text
        assert "guest@example.com" not in response.text and "first_guest" not in response.text
    assert (
        client.post(base, json={"email": "guest@example.com", "role": "admin"}).status_code == 404
    )
    assert (
        client.post(
            base,
            json={
                "email": "guest@example.com",
                "expected_user_id": str(active_id),
                "role": "admin",
            },
        ).status_code
        == 409
    )
    explicit = client.post(base + "/lookup", json={"username": "first_guest", "member_only": True})
    assert explicit.status_code == 200 and explicit.json()["user_id"] == str(active_id)
    assert client.post(base + "/lookup", json={"username": "second_guest"}).status_code == 404
    with sessions() as db:
        assert db.scalar(select(Membership).where(Membership.user_id == active_id)).role == "viewer"


@pytest.mark.parametrize("selector", ["username", "email"])
def test_preview_binding_rejects_reassigned_identifier_then_accepts_fresh_preview(studio, selector):
    client, sessions, ids = studio
    first_id = add_user(sessions)
    second_id = add_user(sessions, username="second_guest", email="second@example.com")
    base = members_url(ids)
    value = "lookup_guest" if selector == "username" else "guest@example.com"
    original = client.post(base + "/lookup", json={selector: value}).json()
    assert original["user_id"] == str(first_id)
    with sessions() as db:
        first = db.get(User, first_id)
        setattr(
            first, selector, "renamed_guest" if selector == "username" else "renamed@example.com"
        )
        db.flush()  # Release the exact unique value before reassigning it.
        setattr(db.get(User, second_id), selector, value)
        db.commit()
    stale = client.post(
        base, json={selector: value, "expected_user_id": original["user_id"], "role": "writer"}
    )
    assert stale.status_code == 409, stale.text
    with sessions() as db:
        assert (
            list(
                db.scalars(select(Membership).where(Membership.user_id.in_([first_id, second_id])))
            )
            == []
        )
    current = client.post(base + "/lookup", json={selector: value}).json()
    assert current["user_id"] == str(second_id)
    added = client.post(
        base, json={selector: value, "expected_user_id": current["user_id"], "role": "writer"}
    )
    assert added.status_code == 200 and added.json()["user_id"] == str(second_id)


@pytest.mark.parametrize("change", ["renamed", "disabled", "ambiguous"])
def test_preview_binding_rechecks_target_before_adding(studio, change):
    client, sessions, ids = studio
    target_id = add_user(sessions)
    base = members_url(ids)
    preview = client.post(base + "/lookup", json={"email": "guest@example.com"}).json()
    with sessions() as db:
        if change == "renamed":
            db.get(User, target_id).email = "changed@example.com"
        elif change == "disabled":
            db.get(User, target_id).is_active = False
        else:
            db.add(User(username="second_guest", email="GUEST@EXAMPLE.COM", display_name="Second"))
        db.commit()
    payload = {"email": "guest@example.com", "role": "writer"}
    assert (
        client.post(base, json={**payload, "expected_user_id": preview["user_id"]}).status_code
        == 409
    )
    assert client.post(base, json=payload).status_code == 404
    with sessions() as db:
        assert db.scalar(select(Membership).where(Membership.user_id == target_id)) is None


def test_duplicate_identifier_add_is_conflict_but_user_id_upsert_remains_compatible(studio):
    client, sessions, ids = studio
    target_id = add_user(sessions)
    base = members_url(ids)
    response = client.post(base, json={"username": "lookup_guest", "role": "viewer"})
    assert response.status_code == 200, response.text
    membership_id = response.json()["id"]
    for selector in ({"username": "lookup_guest"}, {"email": "GUEST@example.com"}):
        for binding in ({}, {"expected_user_id": str(target_id)}):
            duplicate = client.post(base, json={**selector, **binding, "role": "admin"})
            assert duplicate.status_code == 409, duplicate.text
    with sessions() as db:
        assert db.get(Membership, uuid.UUID(membership_id)).role == "viewer"
    updated = client.post(base, json={"user_id": str(target_id), "role": "writer"})
    assert updated.status_code == 200 and updated.json()["id"] == membership_id
    assert updated.json()["role"] == "writer"
    for invalid in (
        {"user_id": str(target_id), "expected_user_id": str(target_id)},
        {"user_id": str(target_id), "email": "guest@example.com"},
        {"expected_user_id": str(target_id)},
    ):
        assert client.post(base, json={**invalid, "role": "writer"}).status_code == 422
    with sessions() as db:
        assert len(list(db.scalars(select(Membership).where(Membership.user_id == target_id)))) == 1
