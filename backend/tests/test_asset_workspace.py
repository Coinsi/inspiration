"""Active shot references and reversible asset removal through the project API."""

import uuid

from sqlalchemy import select

from app.models.identity import Membership
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_asset_reference_drilldown_and_trash_impact(studio):  # noqa: F811
    client, _, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    assets = root + "/assets"
    person = post_ok(client, assets, {"name": "旅人", "type": "character"})
    setting = post_ok(client, assets, {"name": "山口", "type": "location"})
    locked = post_ok(client, assets, {"name": "已锁定", "type": "prop"})
    post_ok(client, assets + f"/{locked['id']}/lock")
    shot = client.get(root + f"/shots/{ids['shot']}").json()
    scene_shots = root + f"/scenes/{shot['scene_id']}/shots"
    other = post_ok(client, scene_shots, {"title": "旅人回望", "description": ""})
    removed = post_ok(client, scene_shots, {"title": "已移除", "description": ""})
    floating = {"asset_id": person["id"], "role": "character"}
    pinned = {
        "asset_id": setting["id"],
        "role": "location",
        "ref_mode": "pinned",
        "pinned_version_id": setting["current_version_id"],
    }
    for row, refs in [
        (shot, [floating, {**floating, "role": "costume"}, pinned]),
        (other, [pinned]),
        (removed, [floating]),
    ]:
        result = client.put(root + f"/shots/{row['id']}/asset-refs", json={"refs": refs})
        assert result.is_success, result.text
    assert client.delete(root + f"/shots/{removed['id']}").is_success

    listing = {a["id"]: a["shot_count"] for a in client.get(assets).json()}
    assert listing == {person["id"]: 1, setting["id"]: 2, locked["id"]: 0}
    assert client.get(assets + "/" + person["id"]).json()["shot_count"] == 1
    usages = client.get(assets + f"/{setting['id']}/usages").json()
    assert [row["shot_title"] for row in usages] == [shot["title"], "旅人回望"]
    assert all(
        row["scene_title"] == "Test scene" and row["scene_id"] == shot["scene_id"] for row in usages
    )
    assert all(row["ref_mode"] == "pinned" and row["pinned_version_no"] == 1 for row in usages)
    selected = {"ids": [person["id"], setting["id"], locked["id"], str(uuid.uuid4())]}
    preview = post_ok(client, assets + "/batch-delete/preview", selected)
    assert set(preview["eligible_ids"]) == {person["id"], setting["id"]}
    assert (preview["shot_count"], preview["floating_shots"], preview["pinned_shots"]) == (2, 1, 2)

    assert client.delete(assets + "/" + locked["id"]).status_code == 423
    assert client.post(root + f"/library/materials/asset/{locked['id']}/trash").status_code == 423
    assert post_ok(client, assets + "/batch-delete", selected) == {"deleted": 2, "skipped": 2}
    assert post_ok(client, assets + "/batch-delete", selected) == {"deleted": 0, "skipped": 4}
    assert {row["id"] for row in client.get(assets + "/trash").json()} == {
        person["id"],
        setting["id"],
    }
    assert client.get(root + f"/shots/{shot['id']}/compose-prompt").status_code == 404
    assert client.get(root + f"/shots/{other['id']}/compose-prompt").is_success
    post_ok(client, root + f"/library/materials/asset/{person['id']}/restore")
    assert client.get(assets + "/" + person["id"]).json()["shot_count"] == 1
    assert client.get(root + f"/shots/{shot['id']}/compose-prompt").is_success


def test_read_only_member_can_inspect_but_cannot_trash(studio):  # noqa: F811
    client, sessions, ids = studio
    assets = f"/api/v1/projects/{ids['project']}/assets"
    asset = post_ok(client, assets, {"name": "旅人", "type": "character"})
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "viewer"
        db.commit()
    path = assets + "/" + asset["id"]
    assert client.get(path + "/usages").is_success
    assert (
        client.post(assets + "/batch-delete/preview", json={"ids": [asset["id"]]}).status_code
        == 403
    )
    assert client.post(assets + "/batch-delete", json={"ids": [asset["id"]]}).status_code == 403
    assert client.delete(path).status_code == 403
    assert client.get(path).is_success
