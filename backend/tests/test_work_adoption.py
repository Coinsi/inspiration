"""Real database checks for ownership, deliberate adoption and pinned editing."""

# ruff: noqa: F811
import uuid

from sqlalchemy import func, select

from app.models.generation import Generation, GenerationJob
from app.models.identity import Membership
from app.models.narrative import Script
from app.models.shot import Shot
from app.models.storage import Blob
from app.models.timeline import Timeline, TimelineItem
from tests.test_media_workflow import post_ok, studio  # noqa: F401
from tests.test_workbench import body


def output(client, root, **changes):
    job = post_ok(client, root + "/workbench/generate", body(**changes))
    assert job["status"] == "succeeded"
    return next(
        g
        for g in client.get(root + "/library/generated").json()["items"]
        if g["job_id"] == job["id"]
    )


def test_work_shared_by_conversations_but_not_messages(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    work = post_ok(client, root + "/works", {"title": "雨夜短片"})
    assert work["script_id"] is None  # A work no longer creates an unrelated empty script.
    a = post_ok(client, root + "/conversations", {"title": "方向甲", "work_id": work["id"]})
    b = post_ok(client, root + "/conversations", {"title": "方向乙", "work_id": work["id"]})
    assert a["id"] != b["id"] and a["work_id"] == b["work_id"]
    path = root + "/conversations/" + a["id"]
    sent = post_ok(
        client,
        path + "/messages",
        {"content": "只讨论开场", "mode": "test", "request_key": str(uuid.uuid4())},
    )
    assert sent["messages"][0]["context"]["work"]["id"] == work["id"]
    assert client.get(root + "/conversations/" + b["id"]).json()["messages"] == []
    assert client.patch(path, json={"work_id": None}).status_code == 409
    g = output(client, root, work_id=work["id"])
    assert g["work"]["id"] == work["id"]
    assert len(client.get(root + f"/workbench/creations?work_id={work['id']}").json()) == 1
    assert client.get(root + "/workbench/creations").json()[0]["work_id"] == work["id"]
    assert client.get(root + f"/generations/{g['id']}/usage").json()["state"] == "candidate"


def test_work_reuses_existing_script_and_rejects_foreign_or_viewer(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    script = client.get(root + "/scripts").json()[0]
    work = post_ok(client, root + "/works", {"title": "已有作品", "script_id": script["id"]})
    assert (
        client.post(root + "/works", json={"title": "重复", "script_id": script["id"]}).status_code
        == 409
    )
    assert (
        client.post(
            root + "/works", json={"title": "空", "script_id": str(uuid.uuid4())}
        ).status_code
        == 404
    )
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Script)) == 1
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="admin"))
        db.commit()
    other = f"/api/v1/projects/{ids['other']}"
    assert client.post(other + "/conversations", json={"work_id": work["id"]}).status_code == 404
    assert (
        client.post(other + "/workbench/generate", json=body(work_id=work["id"])).status_code == 404
    )
    with sessions() as db:
        db.scalar(select(Membership).where(Membership.project_id == ids["project"])).role = "viewer"
        db.commit()
    assert client.post(root + "/works", json={"title": "不能创建"}).status_code == 403


def test_adoption_idempotent_no_file_copy_and_stale_replacement_rejected(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    first, second = output(client, root), output(client, root)
    payload = {"target_type": "shot", "target_id": ids["shot"], "expected_generation_id": None}
    with sessions() as db:
        blobs = db.scalar(select(func.count()).select_from(Blob))
        jobs = db.scalar(select(func.count()).select_from(GenerationJob))
    a = post_ok(client, root + f"/generations/{first['id']}/adopt", payload)
    assert a["cost_points"] == 0 and a["output_blob_hash"] == first["blob_hash"]
    assert post_ok(client, root + f"/generations/{first['id']}/adopt", payload)["id"] == a["id"]
    assert client.post(root + f"/generations/{second['id']}/adopt", json=payload).status_code == 409
    assert client.get(root + f"/generations/{first['id']}/usage").json()["state"] == "adopted"
    cards = client.get(root + "/library/materials").json()["items"]
    assert next(c for c in cards if c["id"] == first["id"])["adopted"]
    assert not next(c for c in cards if c["id"] == second["id"])["adopted"]
    b = post_ok(
        client,
        root + f"/generations/{second['id']}/adopt",
        {**payload, "expected_generation_id": a["id"]},
    )
    assert client.get(root + f"/generations/{first['id']}/usage").json()["state"] == "candidate"
    with sessions() as db:
        assert db.get(Shot, uuid.UUID(ids["shot"])).selected_generation_id == uuid.UUID(b["id"])
        assert not db.get(Generation, uuid.UUID(a["id"])).is_selected
        assert db.scalar(select(func.count()).select_from(Blob)) == blobs
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == jobs
        assert db.scalar(select(func.count()).select_from(Generation)) == 4


def test_replacement_preserves_existing_timeline_and_new_generation_is_candidate(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    first = output(client, root)
    payload = {"target_type": "shot", "target_id": ids["shot"], "expected_generation_id": None}
    selected = post_ok(client, root + f"/generations/{first['id']}/adopt", payload)
    new_timeline = post_ok(client, root + "/timelines", {"name": "新剪辑"})
    path = root + f"/timelines/{new_timeline['id']}"
    response = client.put(
        path + "/items", json={"items": [{"shot_id": ids["shot"], "duration_ms": 1000}]}
    )
    assert response.status_code == 200, response.text
    assert client.get(path + "/document").json()["items"][0]["generation_id"] == selected["id"]
    with sessions() as db:
        t = Timeline(project_id=ids["project"], name="旧剪辑")
        db.add(t)
        db.flush()
        clip = TimelineItem(timeline_id=t.id, shot_id=ids["shot"], ordinal=0, duration_ms=1000)
        db.add(clip)
        db.commit()
        tid, cid = t.id, clip.id
    second = output(client, root)
    with sessions() as db:
        assert str(db.get(Shot, uuid.UUID(ids["shot"])).selected_generation_id) == selected["id"]
    post_ok(
        client,
        root + f"/generations/{second['id']}/adopt",
        {**payload, "expected_generation_id": selected["id"]},
    )
    with sessions() as db:
        assert str(db.get(TimelineItem, cid).generation_id) == selected["id"]
        assert db.get(Timeline, tid).revision == 1


def test_adoption_rejects_deleted_target_trashed_material_and_cross_project(studio):
    from datetime import UTC, datetime

    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    g = output(client, root)
    path = root + f"/generations/{g['id']}/adopt"
    payload = {"target_type": "shot", "target_id": ids["shot"], "expected_generation_id": None}
    assert client.post(path, json={**payload, "target_id": str(uuid.uuid4())}).status_code == 404
    post_ok(client, root + f"/library/materials/generated/{g['id']}/trash")
    assert client.post(path, json=payload).status_code == 409
    post_ok(client, root + f"/library/materials/generated/{g['id']}/restore")
    with sessions() as db:
        db.get(Shot, uuid.UUID(ids["shot"])).deleted_at = datetime.now(UTC)
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="admin"))
        db.commit()
    assert client.post(path, json=payload).status_code == 404
    assert (
        client.post(
            f"/api/v1/projects/{ids['other']}/generations/{g['id']}/adopt", json=payload
        ).status_code
        == 404
    )


def test_asset_cover_uses_image_and_is_reported_as_adopted(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    asset = post_ok(client, root + "/assets", {"name": "角色参考", "type": "character"})
    image = output(client, root)
    body = {"target_type": "asset", "target_id": asset["id"], "expected_generation_id": None}
    selected = post_ok(client, root + f"/generations/{image['id']}/adopt", body)
    assert (
        client.get(root + "/assets/" + asset["id"]).json()["representative_blob_hash"]
        == image["blob_hash"]
    )
    targets = client.get(root + "/adoption-targets").json()
    assert (
        next(t for t in targets if t["target_id"] == asset["id"])["current_generation_id"]
        == selected["id"]
    )
    assert (
        client.get(root + f"/generations/{image['id']}/usage").json()["uses"][0]["target_type"]
        == "asset"
    )
    video = output(client, root)
    with sessions() as db:
        db.get(Generation, uuid.UUID(video["id"])).output_type = "video"
        db.commit()
    assert (
        client.post(
            root + f"/generations/{video['id']}/adopt",
            json={**body, "expected_generation_id": selected["id"]},
        ).status_code
        == 422
    )
