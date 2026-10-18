"""An unavailable extension must survive save/export without becoming executable."""

# ruff: noqa: F811
import uuid

from app.modules.engineering import document_types as registry
from app.modules.engineering import documents
from tests.test_engineering import accept
from tests.test_engineering_packages import request, target
from tests.test_engineering_review import ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_unknown_extension_roundtrip_is_read_only(studio):
    client, _, ids, _, _, _ = ready(studio)
    identity, root = str(uuid.uuid4()), str(uuid.uuid4())
    body = {"custom": {"curves": [1, {"future": "preserve me"}]}}
    nodes = [
        {
            "id": identity,
            "revision": 4,
            "kind": "extension.future-effect",
            "schema_version": 9,
            "title": "未来效果",
            "inputs": {},
            "payload": body,
        },
        {
            "id": root,
            "revision": 1,
            "kind": "work.manifest",
            "schema_version": 1,
            "title": "工程",
            "inputs": {
                "effect": {
                    "target": {"id": identity, "revision": 4},
                    "accepts": "extension.future-effect",
                }
            },
            "payload": {
                "format": 1,
                "outputs": {identity: {"input": "effect", "streams": ["picture"]}},
            },
        },
    ]
    for node in nodes:
        node["content_hash"] = documents.digest(node["inputs"], node["payload"])
    package = {
        "format": "studio.editable-package@1",
        "root": {"id": root, "revision": 1},
        "documents": nodes,
        "includes_media_files": False,
        "external_media": [],
    }
    path = target(client, ids)
    change = post_ok(client, path + "/changes", request(package))
    ref = next(iter(change["snapshot"]["native_roots"].values()))
    detail = client.get(f"{path}/documents/{ref['id']}/revisions/{ref['revision']}").json()
    assert detail["payload"] == body and detail["adapter_available"] is False
    rejected = client.post(
        path + f"/changes/{change['id']}/preview", json={"fingerprint": change["fingerprint"]}
    )
    assert rejected.status_code >= 400
    accept(client, path, change)
    exported = client.get(path + "/package").json()
    unknown = next(n for n in exported["documents"] if n["kind"] == "extension.future-effect")
    assert unknown["payload"] == body and unknown["schema_version"] == 9
    assert unknown["content_hash"] == nodes[0]["content_hash"]
    other = target(client, ids)
    again = post_ok(client, other + "/changes", request(exported))
    accept(client, other, again)
    assert client.get(other + "/package").status_code == 200
    edited = client.post(
        path + "/changes",
        json={
            "request_key": str(uuid.uuid4()),
            "base_revision": 1,
            "commands": [
                {
                    "operation": "document.put",
                    "document_id": ref["id"],
                    "expected_revision": ref["revision"],
                    "kind": unknown["kind"],
                    "schema_version": 9,
                    "title": "edit",
                    "inputs": {},
                    "payload": body,
                }
            ],
        },
    )
    assert edited.status_code >= 400


def test_uninstalled_adapter_does_not_break_existing_work(studio, monkeypatch):
    client, _, _, path, _, _ = ready(studio)
    monkeypatch.setattr(registry, "_TYPES", dict(registry._TYPES))
    # CURRENT is also shared with documents.SUPPORTED; undo individual changes after test.
    kind = "test.removable"
    monkeypatch.setitem(registry.CURRENT, kind, 1)
    registry.register(
        registry.DocumentType(kind, 1, lambda inputs, payload: None, False, True, ("picture",))
    )
    identity = str(uuid.uuid4())
    first = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [
                {
                    "operation": "document.put",
                    "document_id": identity,
                    "expected_revision": 0,
                    "kind": kind,
                    "title": "扩展输出",
                    "payload": {"data": [1, 2, 3]},
                }
            ],
        },
    )
    accept(client, path, first)
    del registry._TYPES[(kind, 1)]
    registry.CURRENT.pop(kind)
    assert client.get(path + "/manifest").status_code == 200
    change = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 1,
            "commands": [
                {
                    "operation": "document.put",
                    "document_id": str(uuid.uuid4()),
                    "expected_revision": 0,
                    "kind": "audio.document",
                    "title": "其他文档",
                    "payload": {"tracks": []},
                }
            ],
        },
    )
    accept(client, path, change)
    package = client.get(path + "/package").json()
    manifest = next(n for n in package["documents"] if n["kind"] == "work.manifest")
    assert manifest["payload"]["outputs"][identity]["streams"] == ["picture"]
    assert next(n for n in package["documents"] if n["kind"] == kind)["payload"] == {
        "data": [1, 2, 3]
    }
