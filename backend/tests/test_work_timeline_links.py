"""Works can reach editing before adoption; explicit links survive sessions."""

# ruff: noqa: F811
import uuid
from datetime import UTC

from app.models.timeline import Timeline
from tests.test_engineering_timeline import prepared
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_link_before_adoption_and_repeat_start(studio):
    client, _, ids, engineering, tid, _ = prepared(studio)
    path = engineering.removesuffix("/engineering")
    assert client.get(path + "/timelines").json() == []
    linked = post_ok(client, path + "/timelines", {"timeline_id": tid})
    assert linked["id"] == tid
    assert post_ok(client, path + "/timelines", {"timeline_id": tid})["id"] == tid
    assert post_ok(client, path + "/timelines", {})["id"] == tid
    assert len(client.get(path + "/timelines").json()) == 1
    assert client.get(engineering).json()["revision"] == 0
    reverse = client.get(
        f"/api/v1/projects/{ids['project']}/works/timeline-links?timeline_id={tid}"
    )
    assert reverse.status_code == 200 and reverse.json()[0]["id"] == path.split("/")[-1]


def test_start_new_and_cross_project_rejected(studio):
    client, sessions, ids, engineering, tid, _ = prepared(studio)
    path = engineering.removesuffix("/engineering")
    new = post_ok(client, path + "/timelines", {})
    assert new["id"] != tid
    assert post_ok(client, path + "/timelines", {})["id"] == new["id"]
    with sessions() as db:
        db.get(Timeline, uuid.UUID(tid)).project_id = ids["other"]
        db.commit()
    assert client.post(path + "/timelines", json={"timeline_id": tid}).status_code == 404


def test_old_candidate_links_are_visible_without_adoption(studio):
    client, _, _, engineering, tid, data = prepared(studio)
    post_ok(client, engineering + "/changes", data)
    assert client.get(engineering).json()["revision"] == 0
    assert (
        client.get(engineering.removesuffix("/engineering") + "/timelines").json()[0]["id"] == tid
    )


def test_work_survives_linked_script_recycling(studio):
    from datetime import datetime

    from sqlalchemy import select

    from app.models.narrative import Script
    from app.models.work import CreativeWork

    client, sessions, ids, engineering, _, _ = prepared(studio)
    path = engineering.removesuffix("/engineering")
    with sessions() as db:
        script = db.scalar(select(Script).where(Script.project_id == uuid.UUID(ids["project"])))
        db.get(CreativeWork, uuid.UUID(path.split("/")[-1])).script_id = script.id
        script.deleted_at = datetime.now(UTC)
        db.commit()
    assert client.get(engineering).status_code == 200
    assert client.get(path + "/timelines").status_code == 200
    assert path.split("/")[-1] in [w["id"] for w in client.get(path.rsplit("/", 1)[0]).json()]
