"""A fixed media instance remains editable/renderable without a storyboard association."""

# ruff: noqa: F811
from tests.test_engineering import accept
from tests.test_engineering_review import ready, preview
from tests.test_media_workflow import studio, post_ok  # noqa: F401
import uuid


def test_independent_clip_render_and_adoption(studio):
    client, _, _, path, tid, url = ready(studio)
    document = client.get(url).json()
    document["items"][0]["shot_id"] = None
    change = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [{"operation": "timeline.edit", "timeline_id": tid, "document": document}],
        },
    )
    rendered = preview(client, path, change)
    assert rendered["preview"]["status"] == "succeeded", rendered
    accept(client, path, change)
    saved = client.get(url).json()
    assert saved["items"][0]["shot_id"] is None
    assert saved["items"][0]["generation_id"] == document["items"][0]["generation_id"]
    saved["items"][0]["generation_id"] = None
    response = client.post(
        path + "/changes",
        json={
            "request_key": str(uuid.uuid4()),
            "base_revision": 1,
            "commands": [{"operation": "timeline.edit", "timeline_id": tid, "document": saved}],
        },
    )
    assert response.status_code == 422
