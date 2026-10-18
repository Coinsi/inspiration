"""Editable package round trips and transactional rejection using real DB."""

# ruff: noqa: F811
import copy
import uuid

from tests.test_engineering import accept
from tests.test_engineering_review import draft, preview, ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def target(client, ids):
    work = post_ok(client, f"/api/v1/projects/{ids['project']}/works", {"title": "导入工程"})
    return f"/api/v1/projects/{ids['project']}/works/{work['id']}/engineering"


def request(package):
    return {
        "request_key": str(uuid.uuid4()),
        "base_revision": 0,
        "commands": [{"operation": "package.import", "package": package}],
    }


def test_sequence_package_reopens_as_independent_editable_timeline(studio):
    client, _, ids, path, tid, url = ready(studio)
    change = draft(client, path, tid, url)
    preview(client, path, change)
    accept(client, path, change)
    package = client.get(path + "/package").json()
    assert package["external_media"] and not package["includes_media_files"]
    destination = target(client, ids)
    data = request(package)
    imported = post_ok(client, destination + "/changes", data)
    assert post_ok(client, destination + "/changes", data)["id"] == imported["id"]
    accept(client, destination, imported)
    timelines = client.get(destination.removesuffix("/engineering") + "/timelines").json()
    assert len(timelines) == 1 and timelines[0]["id"] != tid
    restored = client.get(
        f"/api/v1/projects/{ids['project']}/timelines/{timelines[0]['id']}/document"
    ).json()
    old = client.get(url).json()
    assert restored["items"][0]["duration_ms"] == old["items"][0]["duration_ms"]
    assert restored["items"][0]["id"] != old["items"][0]["id"]
    assert restored["items"][0]["generation_id"] == old["items"][0]["generation_id"]
    # The imported document remains executable through the real existing renderer.
    imported_tid = timelines[0]["id"]
    imported_url = f"/api/v1/projects/{ids['project']}/timelines/{imported_tid}/document"
    next_change = post_ok(
        client,
        destination + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 1,
            "commands": [
                {
                    "operation": "timeline.edit",
                    "timeline_id": imported_tid,
                    "document": client.get(imported_url).json(),
                }
            ],
        },
    )
    assert preview(client, destination, next_change)["preview"]["status"] == "succeeded"


def test_native_package_round_trip_and_tamper_rollback(studio):
    client, _, ids, path, _, _ = ready(studio)
    change = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [
                {
                    "operation": "document.put",
                    "document_id": str(uuid.uuid4()),
                    "expected_revision": 0,
                    "kind": "image.document",
                    "title": "画布",
                    "payload": {"canvas": {"width": 400, "height": 300}, "layers": []},
                }
            ],
        },
    )
    accept(client, path, change)
    package = client.get(path + "/package").json()
    destination = target(client, ids)
    broken = copy.deepcopy(package)
    broken["documents"][0]["payload"]["canvas"]["width"] = 999
    response = client.post(destination + "/changes", json=request(broken))
    assert response.status_code == 422
    assert len(client.get(destination + "/documents").json()["documents"]) == 1
    imported = post_ok(client, destination + "/changes", request(package))
    accept(client, destination, imported)
    exported = client.get(destination + "/package").json()
    original_node = next(n for n in package["documents"] if n["kind"] == "image.document")
    restored_node = next(n for n in exported["documents"] if n["kind"] == "image.document")
    assert restored_node["payload"] == original_node["payload"]
    assert restored_node["id"] != original_node["id"]
