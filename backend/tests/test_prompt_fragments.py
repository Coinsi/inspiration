"""Fragment editing is scoped, revision checked and preserves live identity references."""
import uuid

from app.models.prompt import PromptFragment
from app.models.consistency import VisualIdentity
from app.models.identity import Membership
from tests.test_media_workflow import studio  # noqa: F401


def test_edit_delete_and_stale_revision(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}/prompt-fragments"
    old = client.post(root, json={"name": "Side light", "text": "soft", "category": "lighting"}).json()
    body = {"name": "Morning light", "text": "warm", "category": "lighting", "expected_updated_at": old["updated_at"]}
    response = client.patch(root + "/" + old["id"], json=body)
    assert response.status_code == 200, response.text
    new = response.json()
    assert new["text"] == "warm"
    assert client.patch(root + "/" + old["id"], json=body).status_code == 409
    assert client.delete(root + "/" + old["id"], params={"expected_updated_at": old["updated_at"]}).status_code == 409
    assert client.delete(root + "/" + old["id"], params={"expected_updated_at": new["updated_at"]}).status_code == 204
    assert client.get(root).json() == []
    with sessions() as db:
        assert db.get(PromptFragment, uuid.UUID(old["id"])).deleted_at is not None


def test_fragment_scope_and_readonly(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}/prompt-fragments"
    item = client.post(root, json={"name": "Local", "text": "light"}).json()
    with sessions() as db:
        foreign = PromptFragment(project_id=ids["other"], code="FRG-X", name="Other", text="x")
        db.add(foreign)
        db.commit()
        foreign_id = str(foreign.id)
    body = {"name": "No", "text": "no", "category": "custom", "expected_updated_at": item["updated_at"]}
    assert client.patch(root + "/" + foreign_id, json=body).status_code == 404
    with sessions() as db:
        from sqlalchemy import select
        member = db.scalar(select(Membership).where(Membership.project_id == uuid.UUID(ids["project"])))
        member.role = "viewer"
        db.commit()
    assert client.patch(root + "/" + item["id"], json=body).status_code == 403
    assert client.delete(root + "/" + item["id"], params={"expected_updated_at": item["updated_at"]}).status_code == 403


def test_referenced_fragment_cannot_be_deleted(studio):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    item = client.post(root + "/prompt-fragments", json={"name": "Identity", "text": "consistent"}).json()
    asset = client.post(root + "/assets", json={"name": "Character", "type": "character"}).json()
    with sessions() as db:
        db.add(VisualIdentity(asset_id=uuid.UUID(asset["id"]), prompt_fragment_id=uuid.UUID(item["id"])))
        db.commit()
    response = client.delete(root + "/prompt-fragments/" + item["id"], params={"expected_updated_at": item["updated_at"]})
    assert response.status_code == 409, response.text
    assert len(client.get(root + "/prompt-fragments").json()) == 1
