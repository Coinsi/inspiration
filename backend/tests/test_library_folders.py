"""Folder hierarchy, project boundaries and all-or-nothing media moves."""

# ruff: noqa: F811
import uuid

from sqlalchemy import select

from app.models.identity import Membership
from app.models.library import LibraryMedia
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_folder_hierarchy_conflicts_depth_and_empty_delete(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}/library"
    a = post_ok(client, root + "/folders", {"name": "拍摄原片"})
    b = post_ok(client, root + "/folders", {"name": "海边", "parent_id": a["id"]})
    assert (
        client.post(root + "/folders", json={"name": " 海边 ", "parent_id": a["id"]}).status_code
        == 409
    )
    assert (
        client.put(
            root + f"/folders/{a['id']}",
            json={"name": "拍摄原片", "parent_id": b["id"], "revision": 1},
        ).status_code
        == 409
    )
    changed = client.put(
        root + f"/folders/{b['id']}", json={"name": "清晨海边", "parent_id": a["id"], "revision": 1}
    )
    assert changed.status_code == 200 and changed.json()["revision"] == 2
    assert (
        client.put(
            root + f"/folders/{b['id']}", json={"name": "旧稿", "parent_id": None, "revision": 1}
        ).status_code
        == 409
    )
    assert client.delete(root + f"/folders/{a['id']}?revision=1").status_code == 409
    parent = b["id"]
    for n in range(3, 9):
        parent = post_ok(client, root + "/folders", {"name": f"第{n}层", "parent_id": parent})["id"]
    assert (
        client.post(root + "/folders", json={"name": "第九层", "parent_id": parent}).status_code
        == 409
    )
    c = post_ok(client, root + "/folders", {"name": "另一棵树"})
    post_ok(client, root + "/folders", {"name": "子目录", "parent_id": c["id"]})
    assert (
        client.put(
            root + f"/folders/{c['id']}",
            json={"name": "另一棵树", "parent_id": parent, "revision": 1},
        ).status_code
        == 409
    )
    empty = post_ok(client, root + "/folders", {"name": "可删空目录"})
    assert client.delete(root + f"/folders/{empty['id']}?revision=1").status_code == 200
    assert client.get(root + f"/catalog?folder_id={empty['id']}").status_code == 404


def test_media_batch_move_atomicity_replay_and_catalog(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}/library"
    a = post_ok(client, root + "/folders", {"name": "第一组"})
    b = post_ok(client, root + "/folders", {"name": "第二组"})
    with sessions() as db:
        rows = [LibraryMedia(project_id=ids["project"], name=f"视频{i}") for i in range(2)]
        foreign = LibraryMedia(project_id=ids["other"], name="不能移动")
        db.add_all([*rows, foreign])
        db.flush()
        mids = [str(m.id) for m in rows]
        fid = str(foreign.id)
        db.commit()
    items = [{"media_id": mid, "expected_folder_id": None} for mid in mids]
    bad = client.post(
        root + "/move",
        json={
            "items": [items[0], {"media_id": fid, "expected_folder_id": None}],
            "folder_id": a["id"],
        },
    )
    assert (
        bad.status_code == 404 and client.get(root + "/catalog?root_only=true").json()["total"] == 2
    )
    assert post_ok(client, root + "/move", {"items": items, "folder_id": a["id"]})["moved"] == 2
    assert post_ok(client, root + "/move", {"items": items, "folder_id": a["id"]})["moved"] == 2
    assert client.get(root + f"/catalog?folder_id={a['id']}").json()["total"] == 2
    assert client.get(root + "/catalog?root_only=true").json()["total"] == 0
    post_ok(
        client,
        root + "/move",
        {"items": [{"media_id": mids[0], "expected_folder_id": a["id"]}], "folder_id": b["id"]},
    )
    conflict = client.post(
        root + "/move",
        json={
            "items": [{"media_id": mid, "expected_folder_id": a["id"]} for mid in mids],
            "folder_id": None,
        },
    )
    assert conflict.status_code == 409
    assert client.get(root + f"/catalog?folder_id={a['id']}").json()["total"] == 1
    post_ok(client, root + f"/{mids[1]}/trash")
    assert client.delete(root + f"/folders/{a['id']}?revision=1").status_code == 409
    post_ok(
        client,
        root + "/move",
        {"items": [{"media_id": mids[1], "expected_folder_id": a["id"]}], "folder_id": None},
    )
    assert client.delete(root + f"/folders/{a['id']}?revision=1").status_code == 200
    assert client.get(root + "/catalog?root_only=true&deleted=true").json()["total"] == 1
    assert client.post(root + "/move", json={"items": items * 51}).status_code == 422


def test_folder_scoped_upload_and_permissions(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}/library"
    f = post_ok(client, root + "/folders", {"name": "上传目标"})
    v = post_ok(
        client,
        root + "/uploads",
        {
            "filename": "in-folder.mp4",
            "size_bytes": 100,
            "fingerprint": "a" * 64,
            "folder_id": f["id"],
        },
    )
    assert client.get(root + f"/items/{v['media_id']}").json()["folder_id"] == f["id"]
    assert client.get(root + f"/catalog?folder_id={f['id']}").json()["total"] == 1
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="artist"))
        db.commit()
    other = f"/api/v1/projects/{ids['other']}/library"
    assert client.get(other + f"/catalog?folder_id={f['id']}").status_code == 404
    assert (
        client.post(
            other + "/folders", json={"name": "错误父目录", "parent_id": f["id"]}
        ).status_code
        == 404
    )
    assert (
        client.post(
            other + "/uploads",
            json={
                "filename": "wrong.mp4",
                "size_bytes": 100,
                "fingerprint": "a" * 64,
                "folder_id": f["id"],
            },
        ).status_code
        == 404
    )
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(
                Membership.project_id == uuid.UUID(ids["project"]),
                Membership.user_id == ids["user"],
            )
        )
        member.role = "viewer"
        db.commit()
    assert client.post(root + "/folders", json={"name": "无编辑权限"}).status_code == 403
    assert (
        client.post(
            root + "/move",
            json={"items": [{"media_id": v["media_id"], "expected_folder_id": f["id"]}]},
        ).status_code
        == 403
    )
