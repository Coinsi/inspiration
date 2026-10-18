"""Real PostgreSQL/API checks in disposable schemas; no media rendering."""

# ruff: noqa: F811
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from sqlalchemy import func, select

from app.core.errors import Conflict
from app.models.engineering import EngineeringCommit, EngineeringResource
from app.models.enums import Role
from app.models.generation import Generation, GenerationJob
from app.models.identity import Membership
from app.models.library import LibraryMedia, MediaVersion
from app.models.narrative import Script
from app.models.storage import Blob
from app.modules.engineering import schemas, service
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def setup(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    created = post_ok(client, root + "/works", {"title": "无剧本作品"})
    assert created["script_id"] is None
    with sessions() as db:
        blob = Blob(hash="a" * 64, storage_uri="test", mime="image/png", size_bytes=12)
        db.add(blob)
        media = LibraryMedia(project_id=uuid.UUID(ids["project"]), name="原图")
        db.add(media)
        db.flush()
        version = MediaVersion(
            media_id=media.id,
            ordinal=1,
            filename="image.png",
            fingerprint="a" * 64,
            size_bytes=12,
            uploaded_bytes=12,
            status="ready",
            original_hash=blob.hash,
            media_type="image",
        )
        db.add(version)
        db.flush()
        source = {"version": str(version.id), "media": str(media.id)}
        db.commit()
    return client, sessions, ids, root, created["id"], source


def request(source, revision=0):
    return {
        "request_key": str(uuid.uuid4()),
        "base_revision": revision,
        "commands": [
            {
                "operation": "resource.add",
                "usage_id": str(uuid.uuid4()),
                "source_kind": "library",
                "source_id": source["version"],
                "name": "片头参考",
            }
        ],
    }


def accept(client, path, change):
    return post_ok(
        client, path + f"/changes/{change['id']}/accept", {"fingerprint": change["fingerprint"]}
    )


def test_standalone_work_and_reviewed_commit(studio):
    client, sessions, ids, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    assert any(w["id"] == wid for w in client.get(root + "/works").json())
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Script)) == 1  # only fixture script
    change = post_ok(client, path + "/changes", request(source))
    assert client.get(path).json()["revision"] == 0
    assert accept(client, path, change)["committed_revision"] == 1
    assert accept(client, path, change)["committed_revision"] == 1
    current = client.get(path).json()
    assert current["revision"] == 1 and current["renderable"] is False
    assert current["snapshot"]["resources"][0]["blob_hash"] == "a" * 64
    assert len(client.get(path + "/history").json()) == 1


def test_idempotent_proposal_bound_to_request_content(studio):
    client, _, _, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    data = request(source)
    first = post_ok(client, path + "/changes", data)
    accept(client, path, first)
    assert post_ok(client, path + "/changes", data)["id"] == first["id"]
    data["commands"][0]["name"] = "不同要求"
    assert client.post(path + "/changes", json=data).status_code == 409


def test_stale_candidate_and_wrong_fingerprint_cannot_overwrite(studio):
    client, _, _, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    a = post_ok(client, path + "/changes", request(source))
    b = post_ok(client, path + "/changes", request(source))
    assert (
        client.post(path + f"/changes/{a['id']}/accept", json={"fingerprint": "0" * 64}).status_code
        == 409
    )
    accept(client, path, a)
    assert (
        client.post(
            path + f"/changes/{b['id']}/accept", json={"fingerprint": b["fingerprint"]}
        ).status_code
        == 409
    )
    assert len(client.get(path + "/changes").json()) == 2
    assert client.get(path).json()["revision"] == 1


def test_remove_and_restore_are_new_commits_and_retain_source(studio):
    client, sessions, ids, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    data = request(source)
    accept(client, path, post_ok(client, path + "/changes", data))
    remove = {
        "request_key": str(uuid.uuid4()),
        "base_revision": 1,
        "commands": [{"operation": "resource.remove", "usage_id": data["commands"][0]["usage_id"]}],
    }
    accept(client, path, post_ok(client, path + "/changes", remove))
    assert client.get(path).json()["snapshot"]["resources"] == []
    restore = {
        "request_key": str(uuid.uuid4()),
        "base_revision": 2,
        "commands": [{"operation": "restore", "revision": 1}],
    }
    accept(client, path, post_ok(client, path + "/changes", restore))
    history = client.get(path + "/history").json()
    assert [c["revision"] for c in history] == [3, 2, 1]
    assert history[0]["snapshot"] == history[2]["snapshot"]
    from app.modules.library.service import impact

    with sessions() as db:
        info = impact(db, uuid.UUID(ids["project"]), uuid.UUID(source["media"]))
        assert info["engineering_count"] == 1 and not info["can_trash"]


def test_failed_batch_rolls_back_candidate_and_pins(studio):
    client, sessions, _, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    data = request(source)
    data["commands"].append(
        {**data["commands"][0], "usage_id": str(uuid.uuid4()), "source_id": str(uuid.uuid4())}
    )
    assert client.post(path + "/changes", json=data).status_code == 404
    assert client.get(path + "/changes").json() == []
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(EngineeringResource)) == 0


def test_foreign_source_and_work_are_rejected(studio):
    client, sessions, ids, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    with sessions() as db:
        media = db.get(LibraryMedia, uuid.UUID(source["media"]))
        media.project_id = ids["other"]
        db.commit()
    assert client.post(path + "/changes", json=request(source)).status_code == 404
    assert client.get(path.replace(wid, str(uuid.uuid4()))).status_code == 404


def test_role_revoked_before_adoption(studio):
    client, sessions, ids, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    candidate = post_ok(client, path + "/changes", request(source))
    with sessions() as db:
        membership = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        membership.role = "writer"
        db.commit()
    assert (
        client.post(
            path + f"/changes/{candidate['id']}/accept",
            json={"fingerprint": candidate["fingerprint"]},
        ).status_code
        == 403
    )
    assert client.get(path).json()["revision"] == 0


def test_actual_concurrent_adoption_has_one_winner(studio):
    client, sessions, ids, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    candidates = [post_ok(client, path + "/changes", request(source)) for _ in range(2)]
    ctx = SimpleNamespace(
        project=SimpleNamespace(id=uuid.UUID(ids["project"])),
        user=SimpleNamespace(id=ids["user"]),
        role=Role.admin,
    )

    def submit(c):
        with sessions() as db:
            try:
                result = service.accept(
                    db,
                    ctx,
                    uuid.UUID(wid),
                    uuid.UUID(c["id"]),
                    schemas.Accept(fingerprint=c["fingerprint"]),
                )
                db.commit()
                return result["committed_revision"]
            except Conflict:
                db.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, candidates))
    assert results.count(1) == results.count("conflict") == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(EngineeringCommit)) == 1


def test_generation_result_uses_same_engineering_entry(studio):
    client, sessions, ids, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    with sessions() as db:
        job = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_id=uuid.UUID(ids["shot"]),
            provider="mock",
            status="succeeded",
        )
        db.add(job)
        db.flush()
        g = Generation(
            project_id=job.project_id,
            job_id=job.id,
            target_id=job.target_id,
            provider="mock",
            output_blob_hash="a" * 64,
            output_type="image",
        )
        db.add(g)
        db.flush()
        gid = str(g.id)
        db.commit()
    data = request(source)
    data["commands"][0].update(source_kind="generation", source_id=gid)
    accept(client, path, post_ok(client, path + "/changes", data))
    assert client.get(path).json()["snapshot"]["resources"][0]["source_id"] == gid


def test_source_changed_before_adoption_is_rejected(studio):
    client, sessions, _, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    candidate = post_ok(client, path + "/changes", request(source))
    with sessions() as db:
        db.get(MediaVersion, uuid.UUID(source["version"])).status = "failed"
        db.commit()
    assert (
        client.post(
            path + f"/changes/{candidate['id']}/accept",
            json={"fingerprint": candidate["fingerprint"]},
        ).status_code
        == 422
    )
    assert client.get(path).json()["revision"] == 0
