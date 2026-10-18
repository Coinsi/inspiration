"""Every timeline preview is required; one conflict rolls back the entire adoption."""

# ruff: noqa: F811
import uuid

import pytest

from app.models.timeline import Timeline, TimelineItem
from tests.test_engineering import accept
from tests.test_engineering_review import preview, ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


@pytest.mark.parametrize("conflict", [False, True])
def test_multiple_timelines_adopt_atomically(studio, conflict):
    client, sessions, ids, path, first, first_url = ready(studio)
    original = client.get(first_url).json()
    with sessions() as db:
        second = Timeline(
            project_id=uuid.UUID(ids["project"]),
            name="第二条剪辑",
            kind="rough",
            created_by=ids["user"],
        )
        db.add(second)
        db.flush()
        db.add(
            TimelineItem(
                timeline_id=second.id,
                shot_id=uuid.UUID(ids["shot"]),
                generation_id=uuid.UUID(original["items"][0]["generation_id"]),
                ordinal=0,
                duration_ms=1000,
            )
        )
        second_id = str(second.id)
        db.commit()
    root = f"/api/v1/projects/{ids['project']}"
    urls = {first: first_url, second_id: root + f"/timelines/{second_id}/document"}
    commands = []
    for identity, url in urls.items():
        doc = client.get(url).json()
        doc["items"][0]["duration_ms"] = 600
        commands.append({"operation": "timeline.edit", "timeline_id": identity, "document": doc})
    change = post_ok(
        client,
        path + "/changes",
        {"request_key": str(uuid.uuid4()), "base_revision": 0, "commands": commands},
    )
    assert len(change["timeline_update"]["additional_updates"]) == 1
    assert preview(client, path, change)["preview"]["status"] == "succeeded"
    refused = client.post(
        path + f"/changes/{change['id']}/accept", json={"fingerprint": change["fingerprint"]}
    )
    assert refused.status_code == 409
    assert all(client.get(url).json()["revision"] == 0 for url in urls.values())
    post_ok(
        client,
        path + f"/changes/{change['id']}/preview",
        {"fingerprint": change["fingerprint"], "output_id": "timeline:" + second_id},
    )
    details = client.get(path + f"/changes/{change['id']}").json()
    assert len(details["previews"]) == 2
    assert all(item["status"] == "succeeded" for item in details["previews"])
    if conflict:
        # The last writer conflicts after the first timeline was saved inside
        # the transaction, exercising rollback rather than only early rejection.
        with sessions() as db:
            db.get(Timeline, uuid.UUID(max(urls))).revision += 1
            db.commit()
        response = client.post(
            path + f"/changes/{change['id']}/accept", json={"fingerprint": change["fingerprint"]}
        )
        assert response.status_code == 409
        assert client.get(path).json()["revision"] == 0
        assert all(
            client.get(url).json()["items"][0]["duration_ms"] == 1000 for url in urls.values()
        )
        assert client.get(urls[min(urls)]).json()["revision"] == 0
    else:
        accept(client, path, change)
        assert client.get(path).json()["revision"] == 1
        for url in urls.values():
            saved = client.get(url).json()
            assert saved["revision"] == 1
            assert saved["items"][0]["duration_ms"] == 600
