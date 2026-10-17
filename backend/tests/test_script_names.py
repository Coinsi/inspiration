"""Script naming API: validation, content preservation and project permissions."""

# ruff: noqa: F811
import uuid

import pytest
from sqlalchemy import select

from app.models.identity import Membership
from tests.test_media_workflow import studio  # noqa: F401


def test_create_and_rename_preserve_body_and_revision(studio):
    client, _, ids = studio
    root = f"/api/v1/projects/{ids['project']}/scripts"
    created = client.post(root, json={"title": "  雨夜来信  "})
    assert created.status_code == 200
    assert created.json()["title"] == "雨夜来信"
    path = root + "/" + created.json()["id"]
    body = client.put(
        path + "/blocks", json={"blocks": [{"block_type": "action", "text": "窗外的雨停了。"}]}
    ).json()
    renamed = client.patch(path, json={"title": "  雨停之后  "})
    assert renamed.status_code == 200
    result = renamed.json()
    assert result["title"] == "雨停之后"
    assert result["content_blocks"] == body["content_blocks"]
    assert result["content_revision"] == body["content_revision"]
    assert result["id"] == body["id"] and result["code"] == body["code"]
    assert client.get(path).json()["title"] == "雨停之后"
    assert (
        next(s for s in client.get(root).json() if s["id"] == result["id"])["title"] == "雨停之后"
    )
    assert client.patch(path, json={"title": "字" * 255}).status_code == 200


@pytest.mark.parametrize("title", ["", " \t\n ", "字" * 256])
def test_invalid_names_do_not_create_or_rename(studio, title):
    client, _, ids = studio
    root = f"/api/v1/projects/{ids['project']}/scripts"
    before = client.get(root).json()
    assert client.post(root, json={"title": title}).status_code == 422
    assert client.patch(root + "/" + before[0]["id"], json={"title": title}).status_code == 422
    assert client.get(root).json() == before


def test_rename_respects_membership_project_and_deleted_state(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}/scripts"
    script_id = client.get(root).json()[0]["id"]
    path = root + "/" + script_id
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="admin"))
        db.commit()
    assert (
        client.patch(
            f"/api/v1/projects/{ids['other']}/scripts/{script_id}", json={"title": "不允许"}
        ).status_code
        == 404
    )
    assert client.patch(root + "/" + str(uuid.uuid4()), json={"title": "不存在"}).status_code == 404
    with sessions() as db:
        member = db.scalar(select(Membership).where(Membership.project_id == ids["project"]))
        member.role = "viewer"
        db.commit()
    assert client.patch(path, json={"title": "只读"}).status_code == 403
    with sessions() as db:
        member = db.scalar(select(Membership).where(Membership.project_id == ids["project"]))
        member.role = "admin"
        db.commit()
    assert client.delete(path).status_code == 204
    assert client.patch(path, json={"title": "已删除"}).status_code == 404
