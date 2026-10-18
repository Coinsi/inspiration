"""Native document changes use the same transactional review boundary as editing."""

# ruff: noqa: F811
import uuid

from tests.test_engineering import accept
from tests.test_engineering_review import ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_atomic_native_documents_history_conflict_restore(studio):
    client, _, _, path, _, _ = ready(studio)
    image_id, audio_id = str(uuid.uuid4()), str(uuid.uuid4())
    commands = [
        {
            "operation": "document.put",
            "document_id": image_id,
            "expected_revision": 0,
            "kind": "image.document",
            "title": "海报",
            "payload": {"canvas": {"width": 1920, "height": 1080}, "layers": []},
        },
        {
            "operation": "document.put",
            "document_id": audio_id,
            "expected_revision": 0,
            "kind": "audio.document",
            "title": "混音",
            "payload": {"tracks": []},
        },
    ]

    def propose(base, cmds):
        return post_ok(
            client,
            path + "/changes",
            {
                "request_key": str(uuid.uuid4()),
                "base_revision": base,
                "commands": cmds,
            },
        )

    first = propose(0, commands)
    competing = propose(0, commands)
    assert client.get(path).json()["revision"] == 0
    accept(client, path, first)
    manifest = client.get(path + "/manifest").json()
    assert {d["kind"] for d in manifest["documents"]} == {
        "image.document",
        "audio.document",
        "work.manifest",
    }
    stale = client.post(
        path + "/changes/" + competing["id"] + "/accept",
        json={"fingerprint": competing["fingerprint"]},
    )
    assert stale.status_code == 409
    for command in commands:
        command["expected_revision"] = 1
    commands[0]["payload"]["canvas"]["width"] = 1080
    commands[1]["payload"]["sample_rate"] = 96000
    second = propose(1, commands)
    impact = client.get(path + "/changes/" + second["id"] + "/impact").json()
    assert {o["output"] for o in impact["affected_outputs"]} == {image_id, audio_id}
    assert impact["historical_artifacts_invalidated"] is False
    accept(client, path, second)
    assert client.get(path + "/manifest?revision=1").json() == manifest
    restored = propose(2, [{"operation": "restore", "revision": 1}])
    accept(client, path, restored)
    current = client.get(path + "/manifest").json()
    assert current["manifest_ref"] == manifest["manifest_ref"]
    assert current["documents"] == manifest["documents"]


def test_batch_resolves_new_children_and_rolls_back_late_invalid_document(studio):
    client, _, _, path, _, _ = ready(studio)
    parent, child = str(uuid.uuid4()), str(uuid.uuid4())
    commands = [
        {
            "operation": "document.put",
            "document_id": parent,
            "expected_revision": 0,
            "kind": "image.document",
            "title": "海报合成",
            "inputs": {
                "subject": {"target": {"candidate_document": child}, "accepts": "image.document"}
            },
            "payload": {
                "canvas": {"width": 800, "height": 600},
                "layers": [{"id": str(uuid.uuid4()), "name": "角色", "source": "subject"}],
            },
        },
        {
            "operation": "document.put",
            "document_id": child,
            "expected_revision": 0,
            "kind": "image.document",
            "title": "角色画布",
            "payload": {"canvas": {"width": 400, "height": 300}, "layers": []},
        },
    ]
    request = {"request_key": str(uuid.uuid4()), "base_revision": 0, "commands": commands}
    change = post_ok(client, path + "/changes", request)
    parent_ref = change["snapshot"]["native_roots"][parent]
    fixed = client.get(f"{path}/documents/{parent}/revisions/{parent_ref['revision']}").json()
    assert fixed["inputs"]["subject"]["target"] == change["snapshot"]["native_roots"][child]
    accept(client, path, change)
    before = client.get(path + "/documents").json()
    # Child is written first, then parent's malformed layout fails. Neither may persist.
    commands[1]["expected_revision"] = 1
    commands[1]["payload"]["canvas"]["width"] = 500
    commands[0]["expected_revision"] = 1
    commands[0]["payload"]["canvas"]["width"] = -1
    request.update(request_key=str(uuid.uuid4()), base_revision=1)
    rejected = client.post(path + "/changes", json=request)
    assert rejected.status_code == 422
    assert client.get(path + "/documents").json() == before
    assert client.get(path).json()["revision"] == 1


def test_selected_parent_upgrade_keeps_other_usage_fixed(studio):
    import copy

    client, _, _, path, _, _ = ready(studio)
    child, left, right = [str(uuid.uuid4()) for _ in range(3)]

    def put(identity, inputs=None):
        return {
            "operation": "document.put",
            "document_id": identity,
            "expected_revision": 0,
            "kind": "image.document",
            "title": "画布",
            "inputs": inputs or {},
            "payload": {"canvas": {"width": 16, "height": 16}, "layers": []},
        }

    source = put(child)
    inputs = {"subject": {"target": {"candidate_document": child}, "accepts": "image.document"}}

    def propose(base, commands):
        return post_ok(
            client,
            path + "/changes",
            {"request_key": str(uuid.uuid4()), "base_revision": base, "commands": commands},
        )

    first = propose(0, [source, put(left, inputs), put(right, inputs)])
    accept(client, path, first)
    source = copy.deepcopy(source)
    source["expected_revision"] = 1
    source["payload"]["canvas"]["width"] = 32
    retarget = {
        "operation": "document.retarget",
        "document_id": left,
        "expected_revision": 1,
        "inputs": [
            {
                "alias": "subject",
                "expected": {"id": child, "revision": 1},
                "target": {"candidate_document": child},
            }
        ],
    }
    changed = propose(1, [retarget, source])
    accept(client, path, changed)
    refs = changed["snapshot"]["native_roots"]

    def read(identity):
        return client.get(
            f"{path}/documents/{identity}/revisions/{refs[identity]['revision']}"
        ).json()

    assert read(left)["inputs"]["subject"]["target"] == refs[child]
    assert read(right)["inputs"]["subject"]["target"] == {"id": child, "revision": 1}
    assert refs[right]["revision"] == 1
    # Port preconditions are checked independently of the work-wide revision.
    retarget["expected_revision"] = refs[left]["revision"]
    before = client.get(path + "/documents").json()
    response = client.post(
        path + "/changes",
        json={"request_key": str(uuid.uuid4()), "base_revision": 2, "commands": [retarget]},
    )
    assert response.status_code == 409
    assert client.get(path + "/documents").json() == before
