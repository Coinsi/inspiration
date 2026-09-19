"""Fixed cross-project media grants: real bytes, isolation, retention and serialization."""

# ruff: noqa: F811
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest
from sqlalchemy import func, select

from app.core.deps import get_project_context
from app.models.identity import Membership, User
from app.models.library import MediaReuse
from app.models.storage import Blob
from app.modules.library import reuse, service
from app.modules.library.schemas import ReuseIn
from tests.test_library import library, media_studio, upload  # noqa: F401
from tests.test_media_workflow import post_ok


def setup(library):
    client, sessions, ids, content = library
    source = f"/api/v1/projects/{ids['project']}/library"
    target = f"/api/v1/projects/{ids['other']}/library"
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="artist"))
        db.commit()
    v = upload(client, source, content)
    return client, sessions, ids, content, source, target, v


def test_reuse_same_bytes_version_pin_and_trash_restore(library, monkeypatch):
    client, sessions, ids, content, source, target, v = setup(library)
    from app.storage import cas

    with sessions() as db:
        blob_count = db.scalar(select(func.count()).select_from(Blob))
    # Reuse must not download/re-upload or regenerate media.
    original_put = cas.put_bytes
    monkeypatch.setattr(cas, "put_bytes", lambda *_a, **_k: pytest.fail("Reuse cannot copy bytes"))
    body = {"source_project_id": ids["project"], "source_version_id": v["id"]}
    grant = post_ok(client, target + "/reuse", body)
    again = post_ok(client, target + "/reuse", body)
    assert again["version_id"] == grant["version_id"] and again["reused"]
    current = client.get(target + f"/versions/{grant['version_id']}").json()
    assert current["id"] != v["id"] and current["original_hash"] == v["original_hash"]
    assert current["proxy_hash"] == v["proxy_hash"] and current["duration_ms"] == v["duration_ms"]
    detail = client.get(target + f"/items/{grant['media_id']}").json()
    assert detail["versions"][0]["reuse_origin"]["version_id"] == v["id"]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Blob)) == blob_count
        assert db.scalar(select(func.count()).select_from(MediaReuse)) == 1
    media_url = f"/api/v1/projects/{ids['other']}/blobs/{v['original_hash']}"
    assert client.get(media_url, headers={"Range": "bytes=20-39"}).content == content[20:40]
    impact = client.get(source + f"/{v['media_id']}/deletion-impact").json()
    assert impact["shared_count"] == 1 and not impact["can_trash"]
    assert impact["reuses"][0]["project_id"] == str(ids["other"])
    assert client.post(source + f"/{v['media_id']}/trash").status_code == 409
    post_ok(client, target + f"/{grant['media_id']}/trash")
    assert client.post(target + "/reuse", json=body).status_code == 409
    assert client.get(media_url).status_code == 404
    post_ok(client, source + f"/{v['media_id']}/trash")
    # Restoring the independently granted target does not require a live source project entry.
    post_ok(client, target + f"/{grant['media_id']}/restore")
    assert client.get(media_url, headers={"Range": "bytes=0-9"}).content == content[:10]
    post_ok(client, source + f"/{v['media_id']}/restore")
    monkeypatch.setattr(cas, "put_bytes", original_put)
    newer = upload(client, source, content, v["media_id"])
    assert newer["ordinal"] == 2
    assert len(client.get(target + f"/items/{grant['media_id']}").json()["versions"]) == 1
    assert (
        client.get(target + f"/items/{grant['media_id']}").json()["versions"][0]["reuse_origin"][
            "version_id"
        ]
        == v["id"]
    )


def test_reuse_permissions_and_private_destination_names(library):
    client, sessions, ids, content, source, target, v = setup(library)
    body = {"source_project_id": ids["project"], "source_version_id": v["id"]}
    assert client.post(source + "/reuse", json=body).status_code == 409
    wrong = {**body, "source_project_id": str(ids["other"])}
    assert client.post(source + "/reuse", json=wrong).status_code == 404
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(
                Membership.project_id == ids["project"], Membership.user_id == ids["user"]
            )
        )
        member.role = "viewer"
        db.commit()
    assert client.post(target + "/reuse", json=body).status_code == 403
    assert client.get(target + "/reuse-projects").json() == []
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(
                Membership.project_id == ids["project"], Membership.user_id == ids["user"]
            )
        )
        member.role = "admin"
        db.commit()
    post_ok(client, target + "/reuse", body)
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(
                Membership.project_id == str(ids["other"]), Membership.user_id == ids["user"]
            )
        )
        member.role = "viewer"
        db.commit()
    assert client.post(target + "/reuse", json=body).status_code == 403
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(
                Membership.project_id == str(ids["other"]), Membership.user_id == ids["user"]
            )
        )
        db.delete(member)
        db.commit()
    impact = client.get(source + f"/{v['media_id']}/deletion-impact").json()
    assert impact["shared_count"] == 1
    assert impact["reuses"] == [
        {
            "project_id": None,
            "project_name": None,
            "media_id": None,
            "version_id": None,
            "source_ordinal": 1,
        }
    ]
    assert (
        client.get(f"/api/v1/projects/{ids['other']}/blobs/{v['original_hash']}").status_code == 403
    )


def test_reuse_and_deletion_serialize_and_concurrent_replay(library):
    client, sessions, ids, content, source, target, v = setup(library)
    body = {"source_project_id": ids["project"], "source_version_id": v["id"]}
    with sessions() as db, ThreadPoolExecutor(max_workers=1) as pool:
        ctx = get_project_context(uuid.UUID(str(ids["other"])), db.get(User, ids["user"]), db)
        grant = reuse.create(db, ctx, ReuseIn(**body))
        pending = pool.submit(client.post, source + f"/{v['media_id']}/trash")
        try:
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.3)
        finally:
            db.commit()
        assert pending.result(timeout=5).status_code == 409
        post_ok(client, target + f"/{grant['media_id']}/trash")
        ctx = get_project_context(uuid.UUID(ids["project"]), db.get(User, ids["user"]), db)
        service.trash(db, ctx, uuid.UUID(v["media_id"]))
        db.flush()
        pending = pool.submit(client.post, target + "/reuse", json=body)
        try:
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.3)
        finally:
            db.commit()
        assert pending.result(timeout=5).status_code == 404
    post_ok(client, source + f"/{v['media_id']}/restore")
    post_ok(client, target + f"/{grant['media_id']}/restore")
    newer = upload(client, source, content, v["media_id"])
    body = {**body, "source_version_id": newer["id"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, target + "/reuse", json=body) for _ in range(2)]
        results = [f.result(timeout=10) for f in futures]
        assert all(r.status_code == 200 for r in results)
        assert len({r.json()["version_id"] for r in results}) == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(MediaReuse)) == 2
