"""Native pixels, fixed-job provenance and unsupported-operation rejection."""

# ruff: noqa: F811
import copy
import hashlib
import io
import uuid

import pytest
from PIL import Image

from app.core.errors import CapabilityUnsupported
from app.modules.engineering import documents, image_executor
from tests.test_engineering_review import preview, ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_native_composite_pixels_and_unsupported_blends():
    raw = io.BytesIO()
    Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(raw, format="PNG")
    data = raw.getvalue()
    child, root = str(uuid.uuid4()), str(uuid.uuid4())
    nodes = [
        {
            "id": child,
            "revision": 1,
            "kind": "resource",
            "schema_version": 1,
            "inputs": {},
            "payload": {"media_type": "image", "blob_hash": hashlib.sha256(data).hexdigest()},
        },
        {
            "id": root,
            "revision": 1,
            "kind": "image.document",
            "schema_version": 1,
            "inputs": {"subject": {"target": {"id": child, "revision": 1}, "accepts": "resource"}},
            "payload": {
                "canvas": {"width": 4, "height": 4},
                "layers": [
                    {
                        "id": str(uuid.uuid4()),
                        "name": "角色",
                        "source": "subject",
                        "transform": {"x": 1, "y": 1, "opacity": 0.5},
                    },
                ],
            },
        },
    ]
    for node in nodes:
        node["content_hash"] = documents.digest(node["inputs"], node["payload"])
    body = {
        "executor": image_executor.EXECUTOR,
        "root": {"id": root, "revision": 1},
        "nodes": nodes,
    }
    plan = {**body, "fingerprint": documents.digest({}, body)}
    output = image_executor.render(plan, lambda _: data)
    image = Image.open(io.BytesIO(output))
    assert image.getpixel((0, 0)) == (0, 0, 0, 0)
    assert image.getpixel((1, 1)) == (255, 0, 0, 128)
    unsupported = copy.deepcopy(nodes)
    unsupported[-1]["payload"]["layers"][0]["blend"] = "screen"
    with pytest.raises(CapabilityUnsupported):
        image_executor.preflight(unsupported, body["root"])


def test_native_image_uses_persistent_job_and_does_not_adopt(studio):
    client, sessions, _, path, _, _ = ready(studio)
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
                    "title": "图像预览",
                    "payload": {"canvas": {"width": 320, "height": 180}, "layers": []},
                }
            ],
        },
    )
    result = preview(client, path, change)
    assert result["preview"]["status"] == "succeeded", result
    assert result["preview"]["output_type"] == "image"
    assert result["preview"]["blob_hash"]
    assert client.get(path).json()["revision"] == 0
    repeat = preview(client, path, change)
    assert repeat["preview_job_id"] == result["preview_job_id"]
    from app.models.generation import GenerationJob

    with sessions() as db:
        job = db.get(GenerationJob, uuid.UUID(result["preview_job_id"]))
        assert job.input_snapshot["engineering_plan"]["executor"] == image_executor.EXECUTOR
        assert (
            job.input_snapshot["engineering_plan"]["root"]
            in change["snapshot"]["native_roots"].values()
        )


def test_native_source_resource_preview_and_unsupported_effect_preflight(studio):
    from tests.test_engineering import accept

    client, _, _, path, _, url = ready(studio)
    generation = client.get(url).json()["items"][0]["generation_id"]
    usage = str(uuid.uuid4())
    resources = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [
                {
                    "operation": "resource.add",
                    "usage_id": usage,
                    "source_kind": "generation",
                    "source_id": generation,
                    "name": "源图像",
                }
            ],
        },
    )
    accept(client, path, resources)
    layer = {"id": str(uuid.uuid4()), "name": "源图层", "source": "source"}
    command = {
        "operation": "document.put",
        "document_id": str(uuid.uuid4()),
        "expected_revision": 0,
        "kind": "image.document",
        "title": "合成预览",
        "inputs": {
            "source": {
                "target": resources["snapshot"]["resource_roots"][usage],
                "accepts": "resource",
            }
        },
        "payload": {"canvas": {"width": 320, "height": 180}, "layers": [layer]},
    }
    change = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 1,
            "commands": [command],
        },
    )
    assert preview(client, path, change)["preview"]["status"] == "succeeded"
    command["payload"]["layers"][0]["blend"] = "screen"
    unsupported = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 1,
            "commands": [command],
        },
    )
    response = client.post(
        path + "/changes/" + unsupported["id"] + "/preview",
        json={"fingerprint": unsupported["fingerprint"]},
    )
    assert response.status_code == 422
    assert client.get(path + "/changes/" + unsupported["id"]).json()["preview_job_id"] is None
