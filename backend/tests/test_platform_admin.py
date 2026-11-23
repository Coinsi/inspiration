"""Real isolated PostgreSQL tests; no production credentials or provider calls."""

# ruff: noqa: F811
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select

from app.core.errors import Conflict, QuotaExceeded
from app.core.security import create_access_token
from app.models.generation import GenerationJob, Quota
from app.models.identity import AuditLog, Membership, User
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


def quota_setup(studio):
    client, sessions, ids, basic, normal = setup(studio)
    with sessions() as db:
        db.add_all(
            [
                Membership(project_id=ids["project"], user_id=normal, role="admin"),
                Membership(project_id=ids["other"], user_id=basic, role="admin"),
            ]
        )
        db.commit()
    return client, sessions, ids, basic, normal


def create_limit(client, ids, *, member=None, limit="0", blocked=False):
    response = client.post(
        ROOT + "/quotas",
        json={
            "project_id": ids["project"],
            "user_id": str(member) if member else None,
            "limit": limit,
            "blocked": blocked,
            "reason": "隔离用量测试",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_quota_requires_super_and_valid_project_member(studio):
    client, sessions, ids, basic, normal = quota_setup(studio)
    payload = {"project_id": ids["project"], "limit": "100", "reason": "权限测试"}
    for user in (basic, normal):  # platform operator and project admin are not super admins
        assert client.post(ROOT + "/quotas", headers=token(user), json=payload).status_code == 403
        assert client.get(ROOT + "/quotas/projects", headers=token(user)).status_code == 403
        assert (
            client.get(
                ROOT + f"/quotas/projects/{ids['project']}/members", headers=token(user)
            ).status_code
            == 403
        )
    assert (
        client.post(ROOT + "/quotas", headers={"Authorization": ""}, json=payload).status_code
        == 401
    )
    assert (
        client.post(ROOT + "/quotas", json={**payload, "project_id": str(uuid.uuid4())}).status_code
        == 404
    )
    for invalid_member in (basic, uuid.uuid4()):
        assert (
            client.post(
                ROOT + "/quotas", json={**payload, "user_id": str(invalid_member)}
            ).status_code
            == 422
        )
    assert client.post(ROOT + "/quotas", json={**payload, "limit": "-1"}).status_code == 422
    assert client.post(ROOT + "/quotas", json={**payload, "reason": "  "}).status_code == 422
    choices = client.get(ROOT + f"/quotas/projects/{ids['project']}/members").json()
    assert {item["id"] for item in choices} == {str(ids["user"]), str(normal)}
    assert client.get(ROOT + f"/quotas/projects/{uuid.uuid4()}/members").status_code == 404
    for member in (None, normal):
        created = create_limit(client, ids, member=member, limit="100")
        assert created["scope"] == ("user" if member else "project")
        assert float(created["used"]) == 0 and created["blocked"] is False
        listed = next(
            item
            for item in client.get(ROOT + "/quotas").json()["items"]
            if item["id"] == created["id"]
        )
        assert listed["version"] == created["version"]
        duplicate = client.post(
            ROOT + "/quotas", json={**payload, "user_id": str(member) if member else None}
        )
        assert duplicate.status_code == 409
    with sessions() as db:
        assert len(list(db.scalars(select(Quota)))) == 2
        audit = list(db.scalars(select(AuditLog).where(AuditLog.action == "admin.quota.create")))
        assert len(audit) == 2 and all(entry.user_id == ids["user"] for entry in audit)


@pytest.mark.parametrize("member_scope", [False, True])
def test_concurrent_quota_creation_cannot_duplicate_a_scope(studio, member_scope):
    from app.modules.admin.router import QuotaCreate, create_quota

    _, sessions, ids, _, normal = quota_setup(studio)
    barrier = Barrier(2)
    data = QuotaCreate(
        project_id=ids["project"],
        user_id=normal if member_scope else None,
        limit="100",
        reason="并发隔离测试",
    )

    def create_one(_):
        barrier.wait(timeout=10)
        with sessions() as db:
            try:
                create_quota(data, db, db.get(User, ids["user"]))
                db.commit()
                return "created"
            except Conflict:
                db.rollback()
                return "existing"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(create_one, range(2))) == ["created", "existing"]
    with sessions() as db:
        assert len(list(db.scalars(select(Quota)))) == 1


def test_quota_overview_exposes_only_callers_member_limit(studio):
    client, _, ids, basic, normal = quota_setup(studio)
    project = create_limit(client, ids, limit="100")
    root_member = create_limit(client, ids, member=ids["user"], limit="70")
    normal_member = create_limit(client, ids, member=normal, limit="20")
    path = f"/api/v1/projects/{ids['project']}/quota/overview"
    for user, own, other in (
        (ids["user"], root_member, normal_member),
        (normal, normal_member, root_member),
    ):
        response = client.get(path + f"?user_id={other['user_id']}", headers=token(user))
        assert response.status_code == 200, response.text
        assert response.json()["project"]["id"] == project["id"]
        assert response.json()["member"]["id"] == own["id"]
        assert other["id"] not in response.text
    assert client.get(path, headers=token(basic)).status_code == 403
    assert client.get(path, headers={"Authorization": ""}).status_code == 401


def test_member_reservation_counts_only_own_pending_usage(studio):
    from app.modules.generation import jobs

    client, sessions, ids, _, normal = quota_setup(studio)
    create_limit(client, ids, limit="100")
    member = create_limit(client, ids, member=normal, limit="20")
    with sessions() as db:
        db.get(Quota, uuid.UUID(member["id"])).used_cost = 5
        for user, points in ((normal, 10), (ids["user"], 8)):
            db.add(
                GenerationJob(
                    project_id=ids["project"],
                    created_by=user,
                    target_type="shot",
                    target_id=ids["shot"],
                    provider="mock",
                    request_type="image",
                    status="pending",
                    estimated_cost=points,
                )
            )
        db.commit()
    with sessions() as db:
        with pytest.raises(QuotaExceeded, match="成员"):
            jobs.reserve_check(db, uuid.UUID(ids["project"]), 6, user_id=normal)
        jobs.reserve_check(db, uuid.UUID(ids["project"]), 5, user_id=normal)
        jobs.reserve_check(db, uuid.UUID(ids["project"]), 30, user_id=ids["user"])
        with pytest.raises(QuotaExceeded, match="项目"):
            jobs.reserve_check(db, uuid.UUID(ids["project"]), 83, user_id=ids["user"])
        assert db.get(Quota, uuid.UUID(member["id"])).used_cost == 5


def test_member_usage_accumulates_once_and_pause_is_separate_from_unlimited(studio):
    from app.modules.generation import jobs

    client, sessions, ids, _, normal = quota_setup(studio)
    project = create_limit(client, ids)
    member = create_limit(client, ids, member=normal)
    other_member = create_limit(client, ids, member=ids["user"])
    base = f"/api/v1/projects/{ids['project']}"
    response = client.post(
        base + f"/shots/{ids['shot']}/generate",
        headers=token(normal),
        json={"provider": "mock", "prompt_override": "只用离线模型验证用量"},
    )
    assert response.status_code == 200, response.text
    generated = response.json()
    assert generated["status"] == "succeeded", generated
    with sessions() as db:
        job = db.get(GenerationJob, uuid.UUID(generated["id"]))
        points = job.actual_cost
        assert points > 0 and job.created_by == normal
        jobs.execute(db, job.id)  # redelivery must not double-count completion
    overview = client.get(base + "/quota/overview", headers=token(normal)).json()
    assert overview["member"]["used_cost"] == float(points)
    assert overview["project"]["used_cost"] == float(points)
    assert client.get(base + "/quota/overview").json()["member"]["used_cost"] == 0
    current = next(
        item for item in client.get(ROOT + "/quotas").json()["items"] if item["id"] == member["id"]
    )
    path = ROOT + f"/quotas/{member['id']}"
    paused = client.put(
        path,
        json={
            "expected_version": current["version"],
            "limit": "0",
            "blocked": True,
            "reason": "暂停成员",
        },
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["blocked"] is True and float(paused.json()["used"]) == float(points)
    denied = client.post(
        base + f"/shots/{ids['shot']}/generate",
        headers=token(normal),
        json={"provider": "mock", "prompt_override": "暂停期间不能生成"},
    )
    assert denied.status_code == 402 and "成员生成用量已暂停" in denied.text
    with sessions() as db:
        with pytest.raises(QuotaExceeded, match="成员生成用量已暂停"):
            jobs.reserve_check(db, uuid.UUID(ids["project"]), 0, user_id=normal)
        jobs.reserve_check(db, uuid.UUID(ids["project"]), 999999, user_id=ids["user"])
    # Legacy updates omit the new flag; changing a number must not silently resume usage.
    still_paused = client.put(
        path,
        json={"expected_version": paused.json()["version"], "limit": "100", "reason": "仅调整上限"},
    )
    assert still_paused.status_code == 200, still_paused.text
    assert still_paused.json()["blocked"] is True
    resumed = client.put(
        path,
        json={
            "expected_version": still_paused.json()["version"],
            "limit": "0",
            "blocked": False,
            "reason": "明确恢复不限额",
        },
    )
    assert resumed.status_code == 200, resumed.text
    with sessions() as db:
        jobs.reserve_check(db, uuid.UUID(ids["project"]), 999999, user_id=normal)
        assert db.get(Quota, uuid.UUID(project["id"])).used_cost == points
        assert db.get(Quota, uuid.UUID(member["id"])).used_cost == points
        assert db.get(Quota, uuid.UUID(other_member["id"])).used_cost == 0
        assert len(list(db.scalars(select(GenerationJob)))) == 1
    # A project pause still blocks all members, including free/zero-estimate operations.
    current = next(
        item for item in client.get(ROOT + "/quotas").json()["items"] if item["id"] == project["id"]
    )
    paused = client.put(
        ROOT + f"/quotas/{project['id']}",
        json={
            "expected_version": current["version"],
            "limit": "0",
            "blocked": True,
            "reason": "暂停项目",
        },
    )
    assert paused.status_code == 200
    with sessions() as db:
        for user in (normal, ids["user"]):
            with pytest.raises(QuotaExceeded, match="项目生成用量已暂停"):
                jobs.reserve_check(db, uuid.UUID(ids["project"]), 0, user_id=user)
