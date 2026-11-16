"""Real isolated PostgreSQL tests; no production credentials or provider calls."""

# ruff: noqa: F811
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import select

from app.core.errors import Conflict
from app.core.security import create_access_token
from app.models.generation import GenerationJob, Quota
from app.models.identity import AuditLog, User
from app.modules.admin.accounts import AccountChange, change
from tests.test_library_migrations import library_migration_schema  # noqa: F401
from tests.test_media_workflow import studio  # noqa: F401

ROOT = "/api/v1/admin"


def test_admin_migration_keeps_existing_admins(library_migration_schema):
    from sqlalchemy import text

    from alembic import command

    conn, config, schema = library_migration_schema
    command.upgrade(config, "0056_platform_models")
    admin_id, user_id = uuid.uuid4(), uuid.uuid4()
    for uid, name, enabled in [(admin_id, "legacy_admin", True), (user_id, "legacy_user", False)]:
        conn.execute(
            text(
                'INSERT INTO "user" (id,username,email,display_name,is_active,is_platform_admin) '
                "VALUES (:id,:name,:email,:name,true,:admin)"
            ),
            {"id": uid, "name": name, "email": f"{name}@example.com", "admin": enabled},
        )
    command.upgrade(config, "0057_platform_admin")
    rows = conn.execute(
        text('SELECT id,platform_role,admin_revision,last_login_at FROM "user"')
    ).all()
    assert next(r for r in rows if r.id == admin_id).platform_role == "super"
    assert next(r for r in rows if r.id == user_id).platform_role is None
    assert all(r.admin_revision == 1 and r.last_login_at is None for r in rows)
    command.downgrade(config, "0056_platform_models")
    assert conn.scalar(text('SELECT is_platform_admin FROM "user" WHERE id=:id'), {"id": admin_id})
    command.upgrade(config, "0057_platform_admin")


def test_account_session_updates_do_not_expose_private_profile(studio):
    from app.core.security import hash_password

    client, sessions, ids, basic, normal = setup(studio)
    with sessions() as db:
        db.get(User, normal).password_hash = hash_password("Admin-check-123!")
        db.commit()
    response = client.post(
        "/api/v1/auth/login", json={"email": "ordinary@example.com", "password": "Admin-check-123!"}
    )
    assert response.status_code == 200, response.text
    detail = client.get(ROOT + f"/users/{normal}")
    assert detail.json()["last_login_at"] is not None
    assert "password_hash" not in detail.text and "auth_version" not in detail.text
    assert client.get(ROOT + "/overview").json()["active_users_7d"] == 1


def setup(studio):
    client, sessions, ids = studio
    with sessions() as db:
        root = db.get(User, ids["user"])
        root.is_platform_admin = True
        root.platform_role = "super"
        basic = User(
            username="operator",
            email="operator@example.com",
            display_name="运营管理员",
            is_platform_admin=True,
            platform_role="admin",
        )
        normal = User(username="ordinary", email="ordinary@example.com", display_name="普通用户")
        db.add_all([basic, normal])
        db.flush()
        basic_id, normal_id = basic.id, normal.id
        db.commit()
    return client, sessions, ids, basic_id, normal_id


def token(user_id, version=0):
    return {"Authorization": "Bearer " + create_access_token(str(user_id), {"ver": version})}


def action(client, user_id, action, revision=1, **extra):
    return client.post(
        f"{ROOT}/users/{user_id}/actions",
        json={"expected_revision": revision, "action": action, "reason": "测试账号管理", **extra},
    )


def test_platform_permissions_and_last_super(studio):
    client, sessions, ids, basic, normal = setup(studio)
    for path in ("/users", "/overview", "/tasks", "/system", "/audit", "/quotas"):
        assert client.get(ROOT + path, headers=token(normal)).status_code == 403
        assert client.get(ROOT + path, headers=token(basic)).status_code == 200
    assert (
        client.post(
            f"{ROOT}/users/{normal}/actions",
            headers=token(basic),
            json={"expected_revision": 1, "action": "disable", "reason": "test"},
        ).status_code
        == 403
    )
    assert action(client, ids["user"], "disable").status_code == 409
    assert action(client, ids["user"], "set_role", role="admin").status_code == 409
    assert action(client, normal, "set_role", role="super").status_code == 200
    assert action(client, normal, "set_role", role="user").status_code == 409  # stale form
    assert action(client, ids["user"], "set_role", role="admin").status_code == 200
    assert client.get(ROOT + "/users").status_code == 401  # own old session revoked
    with sessions() as db:
        assert db.get(User, ids["user"]).platform_role == "admin"
        assert db.get(User, normal).platform_role == "super"


def test_account_disable_revoke_audit_and_unknown_fields(studio):
    client, sessions, ids, basic, normal = setup(studio)
    assert client.get("/api/v1/me", headers=token(normal)).status_code == 200
    response = action(client, normal, "disable")
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/me", headers=token(normal)).status_code == 401
    assert action(client, normal, "enable", revision=2).status_code == 200
    assert client.get("/api/v1/me", headers=token(normal)).status_code == 401
    assert client.get("/api/v1/me", headers=token(normal, 2)).status_code == 200
    assert action(client, normal, "revoke_sessions", revision=3).status_code == 200
    assert client.get("/api/v1/me", headers=token(normal, 2)).status_code == 401
    assert action(client, normal, "disable", revision=4, is_platform_admin=True).status_code == 422
    rows = client.get(ROOT + "/audit?action=admin.user").json()["items"]
    assert len(rows) == 3 and all(r["user_id"] == str(ids["user"]) for r in rows)
    assert all(r["reason"] == "测试账号管理" for r in rows)


def test_catalog_paging_filters_and_private_task_payload(studio):
    client, sessions, ids, basic, normal = setup(studio)
    secret = "private-prompt-and-key-should-not-escape"
    with sessions() as db:
        job = GenerationJob(
            project_id=ids["other"],
            target_type="creation",
            target_id=uuid.uuid4(),
            provider="mock",
            request_type="image",
            status="failed",
            created_by=normal,
            input_snapshot={"prompt": secret, "api_key": secret},
            error=f"HTTP 403 https://private.test?token={secret}",
        )
        db.add(job)
        db.flush()
        job_id = job.id
        db.add(
            AuditLog(
                project_id=ids["project"],
                user_id=normal,
                action="private.edit",
                detail={"content": secret},
            )
        )
        db.commit()
    r = client.get(ROOT + f"/tasks?user_id={normal}&state=attention")
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1 and secret not in r.text
    assert r.json()["items"][0]["user_name"] == "普通用户"
    assert r.json()["items"][0]["title"] == "生成图片"
    assert client.get(ROOT + "/tasks?search=普通用户").json()["total"] == 1
    detail = client.get(ROOT + f"/tasks/generation/{job_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["diagnosis"]["http_status"] == "403"
    assert detail.json()["project_url"] is None  # not a member of the other project
    assert secret not in detail.text
    assert "private.edit" not in client.get(ROOT + "/audit").text
    assert client.get(ROOT + "/users?search=ordinary").json()["total"] == 1
    assert client.get(ROOT + "/users?account_role=super").json()["total"] == 1
    assert len(client.get(ROOT + "/users?limit=1&offset=1").json()["items"]) == 1
    assert client.get(ROOT + "/users?limit=101").status_code == 422
    assert client.get(ROOT + "/tasks?kind=unknown").status_code == 422
    assert client.get(ROOT + f"/users/{normal}").json()["generation_usage"]["jobs"] == 1
    assert client.get(ROOT + "/overview").json()["attention_tasks"] == 1


def test_quota_concurrency_and_permissions(studio):
    client, sessions, ids, basic, normal = setup(studio)
    with sessions() as db:
        q = Quota(
            project_id=ids["project"], scope="project", limit_cost=100, used_cost=20, period="total"
        )
        db.add(q)
        db.commit()
    q = client.get(ROOT + "/quotas").json()["items"][0]
    data = {"expected_version": q["version"], "limit": "200.00", "reason": "测试额度"}
    path = ROOT + f"/quotas/{q['id']}"
    assert client.put(path, headers=token(basic), json=data).status_code == 403
    assert client.put(path, json={**data, "limit": "5.00"}).status_code == 409
    assert client.put(path, json=data).status_code == 200
    assert client.put(path, json={**data, "limit": "300.00"}).status_code == 409


def test_concurrent_super_admin_changes_cannot_remove_both(studio):
    client, sessions, ids, basic, normal = setup(studio)
    with sessions() as db:
        db.get(User, basic).platform_role = "super"
        db.commit()
    barrier = Barrier(2)

    def disable_self(uid):
        barrier.wait(timeout=10)
        with sessions() as db:
            try:
                change(
                    db,
                    uid,
                    uid,
                    AccountChange(expected_revision=1, action="disable", reason="parallel test"),
                )
                db.commit()
                return "ok"
            except Conflict:
                db.rollback()
                return "protected"

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(disable_self, [ids["user"], basic]))
    assert sorted(results) == ["ok", "protected"]
    with sessions() as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(User).where(User.is_active.is_(True), User.platform_role == "super")
                    )
                )
            )
            == 1
        )


def test_all_task_kinds_resolve_projects_without_exposing_content(studio):
    from app.models.attachment import Attachment
    from app.models.engineering import EngineeringArchiveJob
    from app.models.harness import ProductionRun
    from app.models.library import LibraryMedia, MediaVersion
    from app.models.media_index import MediaIndex
    from app.models.media_processing import MediaProcessingRun
    from app.models.storage import Blob
    from app.models.transcription import TranscriptionRun
    from app.models.work import CreativeWork

    client, sessions, ids, basic, normal = setup(studio)
    private = "PRIVATE-CONTENT-MUST-NOT-LEAK"
    with sessions() as db:
        media = LibraryMedia(project_id=ids["project"], name=private)
        work = CreativeWork(project_id=ids["project"], title=private, created_by=normal)
        blob = Blob(hash="a" * 64, storage_uri=private, mime="video/mp4", size_bytes=100)
        db.add_all([media, work, blob])
        db.flush()
        version = MediaVersion(
            media_id=media.id,
            ordinal=1,
            filename=private,
            fingerprint="b" * 64,
            size_bytes=100,
            uploaded_bytes=50,
        )
        db.add(version)
        db.flush()
        rows = {
            "upload": version,
            "attachment": Attachment(
                project_id=ids["project"],
                created_by=normal,
                request_key=uuid.uuid4(),
                title=private,
                filename=private,
                kind="video",
                fingerprint="c" * 64,
                size_bytes=100,
                uploaded_bytes=30,
            ),
            "index": MediaIndex(version_id=version.id, model_key=private, total=10, completed=3),
            "processing": MediaProcessingRun(
                version_id=version.id,
                created_by=normal,
                provider="wise",
                source_hash=blob.hash,
                remote_id=private,
            ),
            "transcription": TranscriptionRun(
                project_id=ids["project"],
                version_id=version.id,
                created_by=normal,
                model_key=private,
                total=10,
                completed=2,
            ),
            "archive": EngineeringArchiveJob(
                work_id=work.id,
                created_by=normal,
                request_key=uuid.uuid4(),
                revision=1,
                snapshot={"secret": private},
            ),
            "production": ProductionRun(
                project_id=ids["project"], created_by=normal, request_key=uuid.uuid4(), goal=private
            ),
        }
        db.add_all(rows.values())
        db.flush()
        task_ids = {kind: row.id for kind, row in rows.items()}
        db.commit()
    for kind, task_id in task_ids.items():
        result = client.get(ROOT + f"/tasks/{kind}/{task_id}")
        assert result.status_code == 200, result.text
        assert result.json()["project_id"] == str(ids["project"])
        assert result.json()["project_url"] is not None
        if kind not in {"production", "archive"}:
            assert private not in result.text
        else:
            assert result.json().get("summary") or result.json().get("work_name")
        result = client.get(ROOT + f"/tasks/{kind}/{task_id}", headers=token(basic))
        assert result.json()["project_url"] is None
        assert private not in result.text
        listed = client.get(ROOT + f"/tasks?kind={kind}&project_id={ids['project']}")
        assert listed.json()["total"] == 1 and private not in listed.text
    assert client.get(ROOT + "/overview").json()["active_tasks"] == 7
    with sessions() as db:
        db.get(ProductionRun, task_ids["production"]).status = "needs_plan_approval"
        db.commit()
    assert client.get(ROOT + "/tasks?state=attention").json()["total"] == 1
