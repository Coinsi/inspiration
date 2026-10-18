"""Nested usage editing is copy-on-write, with pixel and archive evidence."""

# ruff: noqa: F811
import copy
import io
import uuid

from PIL import Image

from tests.test_engineering import accept
from tests.test_engineering_review import preview, ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_nested_instance_path_forks_only_selected_occurrence(studio):
    client, sessions, _, path, _, _ = ready(studio)
    root, child, leaf, replacement, left, right, inner = [str(uuid.uuid4()) for _ in range(7)]

    def put(identity, layers, inputs=None, width=2, alpha="straight", output=False):
        return {
            "operation": "document.put",
            "document_id": identity,
            "expected_revision": 0,
            "kind": "image.document",
            "title": "嵌套画布",
            "publish_output": output,
            "inputs": inputs or {},
            "payload": {"canvas": {"width": width, "height": 2, "alpha": alpha}, "layers": layers},
        }

    def link(identity):
        return {"target": {"candidate_document": identity}, "accepts": "image.document"}

    def propose(base, commands):
        return post_ok(
            client,
            path + "/changes",
            {"request_key": str(uuid.uuid4()), "base_revision": base, "commands": commands},
        )

    initial = propose(
        0,
        [
            put(
                root,
                [
                    {"id": left, "name": "左画面", "source": "shared"},
                    {"id": right, "name": "右画面", "source": "shared", "transform": {"x": 2}},
                ],
                {"shared": link(child)},
                width=4,
                output=True,
            ),
            put(
                child, [{"id": inner, "name": "主角", "source": "subject"}], {"subject": link(leaf)}
            ),
            put(leaf, [], alpha="opaque"),
        ],
    )
    accept(client, path, initial)
    assert set(initial["snapshot"]["native_roots"]) == {root}
    command = {
        "operation": "instance.retarget_path",
        "document_id": root,
        "expected_revision": 1,
        "path": [
            {"instance_id": left, "expected": {"id": child, "revision": 1}},
            {"instance_id": inner, "expected": {"id": leaf, "revision": 1}},
        ],
        "target": {"candidate_document": replacement},
    }
    request = {
        "request_key": str(uuid.uuid4()),
        "base_revision": 1,
        "commands": [command, put(replacement, [])],
    }
    change = post_ok(client, path + "/changes", request)
    assert post_ok(client, path + "/changes", request) == change
    assert set(change["snapshot"]["native_roots"]) == {root}
    assert len(change["snapshot"]["derived_documents"]) == 1
    impact = client.get(path + f"/changes/{change['id']}/impact").json()
    assert [
        [step["instance_id"] for step in item["path"]] for item in impact["dependency_paths"]
    ] == [[left], [left, inner]]
    assert impact["dependency_paths_truncated"] is False
    fork = change["snapshot"]["derived_documents"][0]
    assert fork["derived_from"] == {"id": child, "revision": 1}
    fixed = client.get(f"{path}/documents/{root}/revisions/2").json()
    layers = fixed["payload"]["layers"]
    assert fixed["inputs"][layers[0]["source"]]["target"]["id"] == fork["document_id"]
    assert fixed["inputs"][layers[1]["source"]]["target"] == {"id": child, "revision": 1}
    rendered = preview(client, path, change)
    assert rendered["preview"]["status"] == "succeeded", rendered
    from app.models.storage import Blob
    from app.storage import cas

    with sessions() as db:
        raw = cas.read_bytes(db.get(Blob, rendered["preview"]["blob_hash"]))
    image = Image.open(io.BytesIO(raw)).convert("RGBA")
    assert image.getpixel((0, 0)) == (0, 0, 0, 0)
    assert image.getpixel((3, 0)) == (0, 0, 0, 255)
    accept(client, path, change)
    package = client.get(path + "/package").json()
    assert fork["document_id"] in {node["id"] for node in package["documents"]}
    # A stale inner path must not leave any derived document behind.
    before = client.get(path + "/documents").json()
    invalid = copy.deepcopy(command)
    invalid["expected_revision"] = 2
    invalid["path"][0]["expected"] = {"id": fork["document_id"], "revision": 1}
    result = client.post(
        path + "/changes",
        json={"request_key": str(uuid.uuid4()), "base_revision": 2, "commands": [invalid]},
    )
    assert result.status_code == 409
    assert client.get(path + "/documents").json() == before
    # An unreferenced hidden document is not a valid engineering change.
    orphan = put(str(uuid.uuid4()), [])
    result = client.post(
        path + "/changes",
        json={"request_key": str(uuid.uuid4()), "base_revision": 2, "commands": [orphan]},
    )
    assert result.status_code == 409
    assert client.get(path + "/documents").json() == before
    # Restore the exported graph into another work, preserving nested output isolation.
    project_root = path.split("/works/")[0]
    work = post_ok(client, project_root + "/works", {"title": "嵌套工程往返"})
    destination = project_root + f"/works/{work['id']}/engineering"
    imported = post_ok(
        client,
        destination + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [{"operation": "package.import", "package": package}],
        },
    )
    assert len(imported["snapshot"]["native_roots"]) == 1
    output = preview(client, destination, imported)
    assert output["preview"]["status"] == "succeeded", output
    with sessions() as db:
        restored_raw = cas.read_bytes(db.get(Blob, output["preview"]["blob_hash"]))
    assert restored_raw == raw
