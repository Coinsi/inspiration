"""Selected instances split shared ports, while other usages and history stay fixed."""

# ruff: noqa: F811
import copy
import uuid

import pytest

from tests.test_engineering import accept
from tests.test_engineering_review import ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


@pytest.mark.parametrize(
    "kind,slot",
    [
        ("image.document", "source"),
        ("image.document", "mask"),
        ("composition.document", "source"),
        ("audio.document", "source"),
    ],
)
def test_instance_retarget_isolated_atomic_and_versioned(studio, kind, slot):
    client, _, _, path, _, _ = ready(studio)
    parent, child, first, second = [str(uuid.uuid4()) for _ in range(4)]

    def time(n):
        return {"numerator": n, "denominator": 1}

    source_kind = "audio.document" if kind == "audio.document" else "image.document"
    source_payload = (
        {"tracks": []}
        if kind == "audio.document"
        else {"canvas": {"width": 16, "height": 16}, "layers": []}
    )
    items = [
        {"id": identity, "name": "共享素材", "source": "shared"} for identity in (first, second)
    ]
    if slot == "mask":
        for item in items:
            item["mask"] = "shared"
    payload = {"canvas": {"width": 32, "height": 32}, "layers": items}
    if kind == "composition.document":
        payload.update(duration=time(2), frame_rate=time(24))
        for item in items:
            item.update(active={"start": time(0), "end": time(2)}, source_start=time(0))
    if kind == "audio.document":
        for item in items:
            item.pop("name")
            item.update(source_range={"start": time(0), "end": time(1)}, start=time(0))
        payload = {"tracks": [{"id": str(uuid.uuid4()), "name": "对白", "clips": items}]}
    source = {
        "operation": "document.put",
        "document_id": child,
        "expected_revision": 0,
        "kind": source_kind,
        "title": "源文档",
        "payload": source_payload,
    }
    owner = {
        "operation": "document.put",
        "document_id": parent,
        "expected_revision": 0,
        "kind": kind,
        "title": "双实例",
        "payload": payload,
        "inputs": {"shared": {"accepts": source_kind, "target": {"candidate_document": child}}},
    }

    def propose(base, commands):
        return post_ok(
            client,
            path + "/changes",
            {"request_key": str(uuid.uuid4()), "base_revision": base, "commands": commands},
        )

    initial = propose(0, [owner, source])
    accept(client, path, initial)
    historical = client.get(f"{path}/documents/{parent}/revisions/1").json()
    source["expected_revision"] = 1
    if kind == "audio.document":
        source["payload"]["sample_rate"] = 96000
    else:
        source["payload"]["canvas"]["width"] = 24
    command = {
        "operation": "instance.retarget",
        "document_id": parent,
        "expected_revision": 1,
        "instances": [
            {
                "instance_id": first,
                "slot": slot,
                "expected": {"id": child, "revision": 1},
                "target": {"candidate_document": child},
            }
        ],
    }
    change = propose(1, [command, source])
    # Only the selected instance receives a new port and fixed target.
    refs = change["snapshot"]["native_roots"]
    fixed = client.get(f"{path}/documents/{parent}/revisions/{refs[parent]['revision']}").json()
    changed_items = (
        fixed["payload"]["tracks"][0]["clips"]
        if kind == "audio.document"
        else fixed["payload"]["layers"]
    )
    assert changed_items[0][slot] != "shared"
    assert changed_items[1][slot] == "shared"
    assert fixed["inputs"][changed_items[0][slot]]["target"] == refs[child]
    assert fixed["inputs"]["shared"]["target"] == {"id": child, "revision": 1}
    if slot == "mask":
        assert changed_items[0]["source"] == "shared"
    assert client.get(path).json()["revision"] == 1
    impact = client.get(path + f"/changes/{change['id']}/impact").json()
    changes = [item for item in impact["instance_input_changes"] if item["document_id"] == parent]
    assert len(changes) == 1
    assert changes[0]["instance_id"] == first and changes[0]["slot"] == slot
    assert changes[0]["before"] == {"id": child, "revision": 1}
    assert changes[0]["after"] == refs[child]
    accept(client, path, change)
    assert client.get(f"{path}/documents/{parent}/revisions/1").json() == historical
    before = client.get(path + "/documents").json()
    stale = copy.deepcopy(command)
    stale["expected_revision"] = refs[parent]["revision"]
    response = client.post(
        path + "/changes",
        json={"request_key": str(uuid.uuid4()), "base_revision": 2, "commands": [stale]},
    )
    assert response.status_code == 409
    assert client.get(path + "/documents").json() == before
    valid = copy.deepcopy(stale)
    valid["instances"][0]["expected"] = refs[child]
    valid["instances"][0]["target"] = {"id": child, "revision": 1}
    duplicate = copy.deepcopy(valid)
    duplicate["instances"].append(copy.deepcopy(duplicate["instances"][0]))
    missing = copy.deepcopy(valid)
    missing["instances"][0]["target"] = {"id": str(uuid.uuid4()), "revision": 1}
    for invalid in (duplicate, missing):
        response = client.post(
            path + "/changes",
            json={"request_key": str(uuid.uuid4()), "base_revision": 2, "commands": [invalid]},
        )
        assert response.status_code in (404, 409, 422), response.text
        assert client.get(path + "/documents").json() == before
    request = {"request_key": str(uuid.uuid4()), "base_revision": 2, "commands": [valid]}
    restored = post_ok(client, path + "/changes", request)
    assert post_ok(client, path + "/changes", request) == restored
    assert client.get(path).json()["revision"] == 2
