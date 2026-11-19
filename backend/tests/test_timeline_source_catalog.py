"""All selectable editor sources are discoverable and still usable when chosen."""

import uuid

from app.core.security import create_access_token
from app.models.attachment import Attachment
from app.models.identity import Membership, User
from tests.test_conversation_attachments import picture, save, upload
from tests.test_library_references import uploaded  # noqa: F401
from tests.test_media_workflow import post_ok, studio  # noqa: F401
from tests.test_workbench import body


def test_all_sources_paginate_search_and_pin_without_duplicates(uploaded):  # noqa: F811
    client, sessions, ids, root, put = uploaded
    version1 = put(picture(), filename="初稿.png")
    version2 = put(picture(), filename="新版最终.png", media_id=version1["media_id"])
    post_ok(client, root + "/workbench/generate", body(prompt_override="雨后的霓虹街道"))
    generated = client.get(root + "/library/generated").json()["items"][0]["id"]
    attachment = upload(client, ids, filename="窗外.png")
    with sessions() as db:
        save(db, ids, [uuid.UUID(attachment["id"])])
        db.commit()
    catalog = root + "/timeline-sources"
    timeline = post_ok(client, root + "/timelines", {"name": "Sources"})
    edit = root + "/timelines/" + timeline["id"]
    expected = {version1["id"], version2["id"], generated, attachment["id"]}
    first = client.get(catalog, params={"limit": 2}).json()
    second = client.get(catalog, params={"limit": 2, "offset": 2}).json()
    assert first["has_more"] and not second["has_more"]
    assert {x["id"] for x in first["items"] + second["items"]} == expected
    assert (
        client.get(catalog, params={"query": "新版最终"}).json()["items"][0]["id"] == version2["id"]
    )
    assert client.get(catalog, params={"query": "霓虹街道"}).json()["items"][0]["id"] == generated
    assert client.get(catalog, params={"media_type": "audio"}).json()["items"] == []
    picked = []
    for row in first["items"] + second["items"]:
        path = f"{edit}/sources/{row['source_type']}/{row['id']}"
        source = post_ok(client, path, {})
        assert post_ok(client, path, {})["id"] == source["id"]
        if row["source_type"] == "generation":
            assert source["id"] == generated
        picked.append({"generation_id": source["id"], "duration_ms": 1000})
    document = client.get(edit + "/document").json()
    result = client.put(edit + "/document", json={**document, "items": picked})
    assert result.status_code == 200, result.text
    assert len(client.get(edit + "/document").json()["items"]) == len(expected)
    assert {x["id"] for x in client.get(catalog).json()["items"]} == expected
    post_ok(client, root + f"/library/materials/generated/{generated}/trash")
    assert client.get(catalog, params={"query": "霓虹街道"}).json()["items"] == []
    assert client.post(edit + "/sources/generation/" + generated).status_code == 404


def test_draft_hidden_and_other_members_attachments_are_not_editing_sources(uploaded):  # noqa: F811
    client, sessions, ids, root, _ = uploaded
    target = upload(client, ids)
    aid = target["id"]
    timeline = post_ok(client, root + "/timelines", {"name": "Private sources"})
    path = root + f"/timelines/{timeline['id']}/sources/attachment/{aid}"
    catalog = root + "/timeline-sources"
    assert client.get(catalog).json()["items"] == []
    assert client.post(path).status_code == 404
    with sessions() as db:
        save(db, ids, [uuid.UUID(aid)])
        db.commit()
    assert post_ok(client, path, {})["media_type"] == "image"
    with sessions() as db:
        a = db.get(Attachment, uuid.UUID(aid))
        a.hidden_at = a.saved_at
        db.commit()
    assert client.get(catalog).json()["items"] == []
    assert client.post(path).status_code == 404
    with sessions() as db:
        db.get(Attachment, uuid.UUID(aid)).hidden_at = None
        member = User(
            username="another_editor",
            email="editor@example.com",
            password_hash="unused",
            display_name="Editor",
        )
        db.add(member)
        db.flush()
        db.add(Membership(project_id=uuid.UUID(ids["project"]), user_id=member.id, role="admin"))
        uid = member.id
        db.commit()
    client.headers["Authorization"] = f"Bearer {create_access_token(str(uid))}"
    assert client.get(catalog).json()["items"] == []
    assert client.post(path).status_code == 404
    assert client.get(root + "/workbench/references").json()["items"] == []
