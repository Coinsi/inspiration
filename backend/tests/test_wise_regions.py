"""Optional region contracts: real scoped SQL, explicit provider doubles, no models."""

# ruff: noqa: F811
import base64
import io
import uuid

import pytest
from PIL import Image
from pydantic import ValidationError

from app.core.config import settings
from app.models.library import MediaVersion
from app.models.media_processing import MediaProcessingRun
from app.modules.library import optional_capabilities, wise_search
from app.modules.library.schemas import MediaSearchIn, SearchBBox
from tests.test_library import library, media_studio  # noqa: F401
from tests.test_media_processing import setup
from tests.test_media_workflow import post_ok

BOX = {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}


@pytest.mark.parametrize(
    "change",
    [
        {"x": -0.1},
        {"y": 1.1},
        {"w": 0},
        {"h": -0.1},
        {"x": float("nan")},
        {"h": float("inf")},
        {"x": True},
        {"x": "0.1"},
        {"x": 0.9},
        {"y": 0.8},
        {"internal_path": "not a region field"},
    ],
)
def test_rejects_invalid_reference_or_result_boxes(change):
    with pytest.raises(ValidationError):
        SearchBBox.model_validate({**BOX, **change})


@pytest.mark.parametrize(
    "change",
    [
        {"engine": "local"},
        {"mode": "annotated"},
        {"target": "visual"},
        {"target": "audio"},
        {"image": None},
        {"query": "combine"},
        {"audio": "eA=="},
    ],
)
def test_region_only_belongs_to_optional_image_query(change):
    with pytest.raises(ValidationError):
        MediaSearchIn.model_validate(
            {
                "engine": "wise",
                "target": "object",
                "image": "eA==",
                "bbox": BOX,
                **change,
            }
        )


def test_face_requires_reference_before_any_index_lookup():
    with pytest.raises(ValidationError):
        MediaSearchIn(engine="wise", target="face", query="a name")
    assert MediaSearchIn(engine="wise", target="face", image="eA==", bbox=BOX).bbox.w == 0.3


@pytest.mark.parametrize("kind,target", [("video", "object"), ("image", "object")])
def test_multiple_objects_survive_grouping_with_scope_identity_and_bounds(
    library,
    monkeypatch,
    kind,
    target,
):
    # Deliberate protocol-only fixture, not model readiness or inference acceptance.
    monkeypatch.setattr(optional_capabilities, "require_optional", lambda _: None)
    client, sessions, ids, base, version = setup(library, monkeypatch)
    monkeypatch.setattr(settings, "library_wise_capabilities", [target])
    if kind == "image":
        with sessions() as db:
            row = db.get(MediaVersion, uuid.UUID(version["id"]))
            row.media_type, row.duration_ms = "image", None
            db.commit()
    task = post_ok(
        client,
        f"{base}/versions/{version['id']}/process",
        {
            "provider": "wise",
            "capabilities": [target],
        },
    )
    with sessions() as db:
        row = db.get(MediaProcessingRun, uuid.UUID(task["id"]))
        row.status = "ready"
        row.outputs = {"capabilities": {target: {"state": "ready", "vector_count": 2}}}
        db.commit()
    first = {
        "source_id": version["id"],
        "start_seconds": 1,
        "end_seconds": 1,
        "score": 0.8,
        "modality": target,
        "vector_id": "41",
        "bbox": BOX,
    }
    second = {**first, "vector_id": "42", "bbox": {**BOX, "x": 0.6}, "score": 0.7}

    class Remote:
        def __init__(self, *_):
            pass

        def search(self, project, payload):
            assert str(project) == ids["project"]
            assert payload["source_ids"] == [version["id"]]
            return {
                "items": [
                    first,
                    first,
                    second,
                    {**first, "bbox": {**BOX, "x": 0.9}},
                    {**first, "bbox": {**BOX, "x": float("nan")}},
                    {**first, "vector_id": None},
                    {**first, "vector_id": True},
                    {**first, "modality": "audio"},
                    {**first, "source_id": str(uuid.uuid4())},
                ]
            }

    monkeypatch.setattr(wise_search, "WiseClient", Remote)
    request = {"engine": "wise", "target": target, "query": "red object"}
    hits = post_ok(client, f"{base}/search", request)["items"]
    assert len(hits) == 2 and hits[0]["id"] != hits[1]["id"]
    assert [hit["vector_id"] for hit in hits] == ["41", "42"]
    assert hits[0]["bbox"] == BOX
    assert hits[0]["start_ms"] == (None if kind == "image" else 1000)
    assert hits[0]["end_ms"] == (None if kind == "image" else 1500)
    grouped = post_ok(client, f"{base}/search", {**request, "collapse_versions": True})["items"]
    assert len(grouped) == 1 and len(grouped[0]["matches"]) == 2
    assert [match["id"] for match in grouped[0]["matches"]] == [hit["id"] for hit in hits]
    assert post_ok(client, f"{base}/search", {**request, "offset": 1})["items"] == hits[1:]
    other = f"/api/v1/projects/{ids['other']}/library/search"
    assert client.post(other, json=request).status_code == 403


def test_reference_region_uses_exif_oriented_image(library, monkeypatch):
    monkeypatch.setattr(optional_capabilities, "require_optional", lambda _: None)
    client, sessions, _, base, version = setup(library, monkeypatch)
    monkeypatch.setattr(settings, "library_wise_capabilities", ["face"])
    task = post_ok(
        client,
        f"{base}/versions/{version['id']}/process",
        {
            "provider": "wise",
            "capabilities": ["face"],
        },
    )
    with sessions() as db:
        row = db.get(MediaProcessingRun, uuid.UUID(task["id"]))
        row.status = "ready"
        row.outputs = {"capabilities": {"face": {"state": "ready", "vector_count": 1}}}
        db.commit()
    image = Image.new("RGB", (20, 10), "red")
    exif = image.getexif()
    exif[274] = 6  # Browser displays this rotated 90 degrees clockwise.
    buf = io.BytesIO()
    image.save(buf, "JPEG", exif=exif)
    calls = []

    class Remote:
        def __init__(self, *_):
            pass

        def search_media(self, project, content, **kwargs):
            with Image.open(io.BytesIO(content)) as oriented:
                assert oriented.size == (10, 20)
                assert oriented.getexif().get(274) is None
            calls.append(kwargs)
            assert kwargs["bbox"] == BOX and kwargs["modality"] == "face"
            return {"items": []}

    monkeypatch.setattr(wise_search, "WiseClient", Remote)
    response = post_ok(
        client,
        f"{base}/search",
        {
            "engine": "wise",
            "target": "face",
            "image": base64.b64encode(buf.getvalue()).decode(),
            "bbox": BOX,
        },
    )
    assert not response["items"] and len(calls) == 1
