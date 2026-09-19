"""Bounded camera paths and persisted provenance; all DB writes use random test schemas."""

# ruff: noqa: F811
import copy
import io
import uuid

import pytest
from PIL import Image
from pydantic import ValidationError

from app.modules.director.schemas import Document
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def path_document():
    d = Document().model_dump(mode="json")
    d["camera_keyframes"] = [
        {
            "id": str(uuid.uuid4()),
            "at": 0.4,
            "hold": 0.1,
            "ease": "linear",
            "camera": {"position": [0, 3, 7], "target": [0, 1, 0], "fov": 42},
        },
        {
            "id": str(uuid.uuid4()),
            "at": 0.75,
            "hold": 0,
            "camera": {"position": [-3, 3, 6], "target": [0, 1, 0], "fov": 40},
        },
    ]
    d["camera_hold"] = 0.05
    return d


@pytest.mark.parametrize(
    "invalid",
    ["order", "duplicate", "endpoint", "hold", "start_hold", "count", "nan", "cross_target"],
)
def test_invalid_camera_paths(invalid):
    d = path_document()
    if invalid == "order":
        d["camera_keyframes"].reverse()
    elif invalid == "duplicate":
        d["camera_keyframes"][1]["id"] = d["camera_keyframes"][0]["id"]
    elif invalid == "endpoint":
        d["camera_keyframes"][0]["at"] = 0
    elif invalid == "hold":
        d["camera_keyframes"][0]["hold"] = 0.35
    elif invalid == "start_hold":
        d["camera_hold"] = 0.4
    elif invalid == "count":
        d["camera_keyframes"] *= 6
    elif invalid == "nan":
        d["camera_keyframes"][0]["at"] = float("nan")
    else:
        d["camera"] = {"position": [0, 1, -7], "target": [0, 1, 0], "fov": 40}
        d["camera_keyframes"][0]["camera"] = {"position": [0, 1, 7], "target": [0, 1, 0], "fov": 40}
    with pytest.raises(ValidationError):
        Document.model_validate(d)


def test_old_documents_and_duration_scaling():
    assert Document.model_validate({}).camera_keyframes == []
    d = path_document()
    for duration in [1, 5, 15]:
        assert len(Document.model_validate({**d, "duration": duration}).camera_keyframes) == 2


def test_saved_motion_history_and_reference(studio):
    client, _, ids = studio
    root = f"/api/v1/projects/{ids['project']}/director-scenes"
    scene = post_ok(client, root, {"name": "三段运镜"})
    doc = path_document()
    response = client.put(
        f"{root}/{scene['id']}", json={"name": scene["name"], "revision": 0, "document": doc}
    )
    assert response.status_code == 200, response.text
    saved = response.json()["document"]
    assert len(saved["camera_keyframes"]) == 2
    assert client.get(f"{root}/{scene['id']}").json()["document"] == saved
    invalid = copy.deepcopy(saved)
    invalid["camera_keyframes"].reverse()
    assert (
        client.put(
            f"{root}/{scene['id']}",
            json={"name": scene["name"], "revision": 1, "document": invalid},
        ).status_code
        == 422
    )
    assert client.get(f"{root}/{scene['id']}").json()["revision"] == 1
    image = io.BytesIO()
    Image.new("RGB", (160, 90), "navy").save(image, "PNG")
    result = client.post(
        f"{root}/{scene['id']}/references",
        data={
            "revision": 1,
            "shot_id": ids["shot"],
            "request_key": str(uuid.uuid4()),
            "progress": 0.45,
        },
        files={"file": ("motion.png", image.getvalue(), "image/png")},
    )
    assert result.status_code == 200, result.text
    assert result.json()["input_refs"]["document"] == saved
    post_ok(client, f"{root}/{scene['id']}/history/0/restore", {"revision": 1})
    restored = post_ok(client, f"{root}/{scene['id']}/history/1/restore", {"revision": 2})
    assert restored["revision"] == 3 and restored["document"] == saved
