"""Human identity corrections change current retrieval without rewriting historical evidence."""

# ruff: noqa: F811
import uuid

from sqlalchemy import select

from app.models.identity import Membership
from app.models.library import LibraryMedia, MediaVersion
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def seed(sessions, ids):
    with sessions() as db:
        m = LibraryMedia(project_id=uuid.UUID(ids["project"]), name="第一集")
        db.add(m)
        db.flush()
        v = MediaVersion(
            media_id=m.id,
            ordinal=1,
            filename="ep1.mp4",
            fingerprint="a" * 64,
            size_bytes=1,
            uploaded_bytes=1,
            status="ready",
            duration_ms=10000,
        )
        db.add(v)
        db.commit()
        return str(m.id), str(v.id)


def test_identity_correction_history_and_visual_overlap(studio):
    c, sessions, ids = studio
    m, v = seed(sessions, ids)
    root = f"/api/v1/projects/{ids['project']}"
    base = root + "/identity-evidence"
    a = post_ok(
        c, base + "/identities", {"name": "林舟", "aliases": ["小林"], "description": "故事主角"}
    )
    b = post_ok(c, base + "/identities", {"name": "周岚"})
    data = {
        "identity_id": a["id"],
        "version_id": v,
        "start_ms": 1000,
        "end_ms": 3000,
        "kind": "visual",
        "status": "confirmed",
        "observation": "蓝衣人物在桌旁",
        "note": "已核对镜头",
    }
    e = post_ok(c, base, data)
    post_ok(
        c, base, {**data, "identity_id": b["id"], "kind": "speech", "observation": "台词提到周岚"}
    )
    pair = f"{base}?identity_id={a['id']}&with_identity={b['id']}"
    assert c.get(pair).json()["total"] == 0
    post_ok(
        c,
        base,
        {
            **data,
            "identity_id": b["id"],
            "start_ms": 2500,
            "end_ms": 4000,
            "observation": "另一个人物进入画面",
        },
    )
    assert c.get(pair).json()["total"] == 1
    correction = {k: data[k] for k in ("kind", "status", "observation", "note")}
    correction.update(identity_id=None, revision=0, note="身份无法确认，撤回原来的关联")
    r = c.put(base + "/" + e["id"], json=correction)
    assert r.status_code == 200, r.text
    assert c.get(f"{base}?identity_id={a['id']}").json()["total"] == 0
    assert c.get(base + "?unknown=true").json()["total"] == 1
    assert c.get(pair).json()["total"] == 0
    assert c.put(base + "/" + e["id"], json=correction).status_code == 409
    hist = c.get(base + "/" + e["id"] + "/history").json()
    assert len(hist) == 2 and hist[-1]["document"]["identity_id"] == a["id"]
    restored = post_ok(c, base + "/" + e["id"] + "/history/0/restore", {"revision": 1})
    assert restored["revision"] == 2 and restored["identity_id"] == a["id"]
    assert c.get(pair).json()["total"] == 1
    assert c.get(f"{base}?identity_id={a['id']}&with_identity={a['id']}").status_code == 422
    assert (
        c.put(
            base + "/identities/" + a["id"],
            json={"name": "林舟（少年）", "aliases": [], "description": "", "revision": 0},
        ).status_code
        == 200
    )
    assert (
        c.put(base + "/identities/" + a["id"], json={"name": "陈旧修改", "revision": 0}).status_code
        == 409
    )


def test_evidence_retention_permissions_and_bounds(studio):
    c, sessions, ids = studio
    m, v = seed(sessions, ids)
    root = f"/api/v1/projects/{ids['project']}"
    base = root + "/identity-evidence"
    data = {
        "version_id": v,
        "start_ms": 0,
        "end_ms": 1000,
        "observation": "未知人物",
        "kind": "visual",
        "status": "proposed",
    }
    assert c.post(base, json={**data, "end_ms": 20000}).status_code == 422
    assert c.post(base, json={**data, "identity_id": str(uuid.uuid4())}).status_code == 404
    assert c.post(base, json={**data, "version_id": str(uuid.uuid4())}).status_code == 404
    e = post_ok(c, base, data)
    impact = c.get(root + f"/library/{m}/deletion-impact")
    assert impact.status_code == 200, impact.text
    assert impact.json()["evidence_count"] == 1 and not impact.json()["can_trash"]
    assert c.post(root + f"/library/{m}/trash").status_code == 409
    assert (
        c.put(
            base + "/" + e["id"],
            json={
                "revision": 0,
                "identity_id": None,
                "kind": "visual",
                "status": "rejected",
                "observation": "无人物，误判",
                "note": "人工纠错",
            },
        ).status_code
        == 200
    )
    assert c.get(root + f"/library/{m}/deletion-impact").json()["can_trash"]
    with sessions() as db:
        db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        ).role = "viewer"
        db.commit()
    assert c.post(base, json=data).status_code == 403
    assert c.get(base).status_code == 200
