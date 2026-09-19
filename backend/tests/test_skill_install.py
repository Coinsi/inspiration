"""Installation persists packages once, including after a lost response."""

# ruff: noqa: F811
import uuid

from sqlalchemy import func, select

from app.models.skill import CreativeSkillVersion
from app.modules.skill.packages import parse_package
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_skill_packages import package


def test_markdown_extension():
    parsed = parse_package(b"# Test skill\n\nRead scene context.", "method.markdown")
    assert parsed["name"] == "Test skill"


def test_install_retry_and_version_history(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    body = {"request_key": str(uuid.uuid4()), "document": package()}
    first = client.post(root + "/skills/install", json=body)
    assert first.status_code == 200, first.text
    skill = first.json()
    assert skill["revision"] == 1
    assert skill["files"] == body["document"]["files"]
    retry = client.post(root + "/skills/install", json=body)
    assert retry.status_code == 200 and retry.json()["id"] == skill["id"]
    assert client.get(root + "/skills/catalog").json()["total"] == 1
    with sessions() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(CreativeSkillVersion)
                .where(CreativeSkillVersion.skill_id == uuid.UUID(skill["id"]))
            )
            == 1
        )
    changed = {**body, "document": {**body["document"], "name": "修改后的内容"}}
    assert client.post(root + "/skills/install", json=changed).status_code == 409
    assert client.get(root + f"/skills/{skill['id']}").json()["name"] == skill["name"]
    # A late retry does not overwrite a newer edit.
    update = {**body["document"], "revision": 1, "instructions": "安装后修改"}
    assert client.put(root + f"/skills/{skill['id']}", json=update).status_code == 200
    late = client.post(root + "/skills/install", json=body)
    assert late.json()["revision"] == 2 and late.json()["instructions"] == "安装后修改"
    assert (
        client.post(
            root + "/skills/install",
            json={
                **body,
                "document": {**body["document"], "files": [{"path": "../bad", "content": "x"}]},
            },
        ).status_code
        == 422
    )
    assert (
        client.post(f"/api/v1/projects/{ids['other']}/skills/install", json=body).status_code == 403
    )
