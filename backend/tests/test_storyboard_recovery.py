"""Scene-scoped storyboard operations on isolated PostgreSQL schemas."""

# ruff: noqa: F811
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.models.generation import GenerationJob
from app.models.identity import AuditLog, Membership
from app.models.library import LibraryMedia, MediaVersion
from app.models.narrative import Scene, Script
from app.models.shot import Shot
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_removal_and_recovery_preserve_production_and_references(studio):
    client, sessions, ids = studio
    project = uuid.UUID(ids["project"])
    root = f"/api/v1/projects/{project}"
    shot_id = ids["shot"]
    path = root + f"/shots/{shot_id}"
    scene_id = client.get(path).json()["scene_id"]
    shots = root + f"/scenes/{scene_id}/shots"
    other = post_ok(client, shots, {"title": "余下的镜头"})
    asset = post_ok(client, root + "/assets", {"name": "旅人", "type": "character"})
    ref = client.put(path + "/asset-refs", json={"refs": [{"asset_id": asset["id"]}]}).json()[0]
    assert post_ok(client, path + "/generate", {"provider": "mock"})["status"] == "succeeded"
    generation = client.get(
        root + "/generations", params={"target_id": shot_id, "target_type": "shot"}
    ).json()[0]
    assert client.post(root + f"/generations/{generation['id']}/select").is_success
    review = post_ok(
        client, root + "/reviews", {"target_id": shot_id, "generation_id": generation["id"]}
    )
    with sessions.begin() as db:
        media = LibraryMedia(project_id=project, name="原片段")
        db.add(media)
        db.flush()
        version = MediaVersion(
            media_id=media.id,
            ordinal=1,
            filename="scene.mp4",
            fingerprint="a" * 64,
            size_bytes=10,
            uploaded_bytes=10,
            status="ready",
            duration_ms=2500,
        )
        db.add(version)
        db.flush()
        version_id = str(version.id)
        original_jobs = db.scalar(select(func.count()).select_from(GenerationJob))
    usage = post_ok(
        client,
        root + "/library/usages",
        {
            "shot_id": shot_id,
            "version_id": version_id,
            "start_ms": 100,
            "end_ms": 1600,
            "purpose": "visual_reference",
        },
    )
    selection = {"shot_ids": [shot_id, other["id"], shot_id]}
    assert post_ok(client, shots + "/remove", selection) == {"changed": 2}
    assert post_ok(client, shots + "/remove", selection) == {"changed": 0}
    assert client.delete(path).status_code == 204
    assert client.get(path).status_code == 404
    assert client.get(shots).json() == [] and client.get(root + "/shots").json() == []
    assert client.get(root + "/shots/board").json()["total"] == 0
    assert client.get(root + "/scenes").json()[0]["shot_count"] == 0
    assert {x["id"] for x in client.get(shots + "/removed").json()} == {shot_id, other["id"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        restored = list(
            pool.map(lambda _: post_ok(client, shots + "/restore", selection)["changed"], range(2))
        )
    assert sorted(restored) == [0, 2]
    assert [x["id"] for x in client.get(shots).json()] == [shot_id, other["id"]]
    assert client.get(shots + "/removed").json() == []
    assert client.get(root + "/scenes").json()[0]["shot_count"] == 2
    assert client.get(path).json()["selected_generation_id"] == generation["id"]
    assert client.get(path + "/asset-refs").json()[0] == ref
    assert client.get(root + "/library/usages", params={"shot_id": shot_id}).json()[0] == usage
    assert (
        client.get(root + "/reviews", params={"target_id": shot_id, "target_type": "shot"}).json()[
            0
        ]
        == review
    )
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == original_jobs
        for action in ("shot.delete", "shot.restore"):
            assert (
                db.scalar(
                    select(func.count()).select_from(AuditLog).where(AuditLog.action == action)
                )
                == 2
            )


def test_all_or_nothing_scope_permissions_and_legacy_delete(studio):
    client, sessions, ids = studio
    project = uuid.UUID(ids["project"])
    root = f"/api/v1/projects/{project}"
    shot_id = ids["shot"]
    path = root + f"/shots/{shot_id}"
    scene_id = client.get(path).json()["scene_id"]
    shots = root + f"/scenes/{scene_id}/shots"
    with sessions.begin() as db:
        existing = db.get(Scene, uuid.UUID(scene_id))
        same = Scene(
            project_id=project, code="SC-2", title="别场", ordinal=2, script_id=existing.script_id
        )
        foreign_script = Script(project_id=ids["other"], code="SCR-1", title="另一个剧本")
        db.add(foreign_script)
        db.flush()
        foreign = Scene(
            project_id=ids["other"],
            script_id=foreign_script.id,
            code="SC-1",
            title="其他项目",
            ordinal=1,
        )
        db.add_all([same, foreign])
        db.flush()
        others = [
            Shot(project_id=sc.project_id, scene_id=sc.id, code="SH-1", ordinal=1)
            for sc in (same, foreign)
        ]
        db.add_all(others)
        db.flush()
        bad_ids = [str(s.id) for s in others] + [str(uuid.uuid4())]
        foreign_scene_id = str(foreign.id)
    for bad in bad_ids:
        assert client.post(shots + "/remove", json={"shot_ids": [shot_id, bad]}).status_code == 404
        assert client.get(path).is_success
    assert client.get(root + f"/scenes/{foreign_scene_id}/shots/removed").status_code == 404
    assert client.delete(path).status_code == 204
    for bad in bad_ids:
        assert client.post(shots + "/restore", json={"shot_ids": [shot_id, bad]}).status_code == 404
        assert client.get(path).status_code == 404
    with sessions.begin() as db:
        db.scalar(select(Membership).where(Membership.project_id == project)).role = "viewer"
    assert client.get(shots + "/removed").is_success
    for action in ("remove", "restore", "reorder"):
        assert client.post(shots + "/" + action, json={"shot_ids": [shot_id]}).status_code == 403
    assert client.delete(path).status_code == 403
    assert client.get(path).status_code == 404


def test_archived_numbering_reorder_collisions_and_breakdown(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    original = client.get(root + f"/shots/{ids['shot']}").json()
    scope = root + f"/scenes/{original['scene_id']}"
    shots = scope + "/shots"
    first = post_ok(client, shots, {"title": "留存编号"})
    later = post_ok(client, shots, {"title": "后来的镜头"})
    assert client.delete(root + f"/shots/{first['id']}").status_code == 204
    # Historical datasets can contain a reused code and position after an older reorder.
    with sessions.begin() as db:
        db.get(Shot, uuid.UUID(first["id"])).code = later["code"]
    post_ok(client, shots + "/reorder", {"shot_ids": [original["id"], later["id"]]})
    post_ok(client, shots + "/restore", {"shot_ids": [first["id"]]})
    restored = client.get(shots).json()
    assert [s["id"] for s in restored] == [original["id"], later["id"], first["id"]]
    assert restored[1]["code"] == later["code"]
    assert len({s["code"] for s in restored}) == len({s["ordinal"] for s in restored}) == 3
    post_ok(client, shots + "/remove", {"shot_ids": [s["id"] for s in restored]})
    rebuilt = post_ok(client, scope + "/breakdown?strategy=mock", {})
    assert rebuilt and all(s["ordinal"] > max(x["ordinal"] for x in restored) for s in rebuilt)
    with ThreadPoolExecutor(max_workers=2) as pool:
        new = list(pool.map(lambda i: post_ok(client, shots, {"title": str(i)}), range(2)))
    all_shots = restored + rebuilt + new
    assert len({s["code"] for s in all_shots}) == len(all_shots)
    assert len({s["ordinal"] for s in all_shots}) == len(all_shots)


def test_editing_other_references_retains_trashed_asset_and_its_original_role(studio):
    client, _, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    shot = client.get(root + f"/shots/{ids['shot']}").json()
    refs = root + f"/shots/{shot['id']}/asset-refs"
    old = post_ok(client, root + "/assets", {"name": "旧外套纽扣", "type": "prop"})
    other = post_ok(client, root + "/assets", {"name": "同行者", "type": "character"})
    selected = client.put(
        refs,
        json={
            "refs": [
                {"asset_id": old["id"], "role": "prop", "usage": {"aspects": ["appearance"]}},
                {
                    "asset_id": old["id"],
                    "role": "costume",
                    "ref_mode": "pinned",
                    "pinned_version_id": old["current_version_id"],
                },
                {"asset_id": other["id"], "role": "character"},
            ]
        },
    ).json()
    assert client.delete(root + f"/assets/{old['id']}").status_code == 204
    stored = client.get(refs).json()
    assert [(r["asset_name"], r["asset_removed"]) for r in stored] == [
        ("旧外套纽扣", True),
        ("旧外套纽扣", True),
        ("同行者", False),
    ]
    assert (
        client.put(refs, json={"refs": [{**stored[0], "usage": {"look": "修改"}}]}).status_code
        == 409
    )
    assert client.get(refs).json() == stored
    assert client.put(refs, json={"refs": stored[:2]}).json() == stored[:2]
    pinned = client.put(refs, json={"refs": [stored[1]]}).json()[0]
    assert pinned["id"] == selected[1]["id"] and pinned["ordinal"] == 0
    assert pinned["pinned_version_id"] == old["current_version_id"]
    assert client.put(refs, json={"refs": [pinned, stored[0]]}).status_code == 404
    assert client.get(refs).json() == [pinned]
