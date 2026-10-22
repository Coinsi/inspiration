"""Mixed references through the real API/worker; the paid provider is stubbed."""

# ruff: noqa: F811
import uuid

import pytest

from app.adapters.contracts import GenerationResult, Output
from app.adapters.generation.video_v1 import VideoV1Provider
from app.models.generation import Generation, GenerationJob
from app.modules.generation import media_engine
from tests.test_creative_models import group
from tests.test_media_workflow import changing_video, studio  # noqa: F401
from tests.test_model_channels import add, setup


def test_mixed_video_references_reach_wire_and_keep_provenance(studio, changing_video, monkeypatch):
    client, sessions, ids = studio
    client.base_url = "https://studio.example.com"
    root, channel = setup(client, ids["project"])
    profile = add(
        client,
        root,
        channel,
        name="Mixed video",
        model="S-2.5-满血",
        protocol="video_v1",
        modality="video",
        max_reference_images=1,
        max_reference_videos=1,
        parameters={"duration": [4], "resolution": ["720p"]},
    )
    model = group(client, root, [profile], name="Mixed routed", modality="video")
    uploaded = []
    for name, data, mime in [
        ("reference.png", media_engine.mock_image(), "image/png"),
        ("reference.mp4", changing_video, "video/mp4"),
    ]:
        r = client.post(
            root + f"/media/shot/{ids['shot']}/upload", files={"file": (name, data, mime)}
        )
        assert r.status_code == 200, r.text
        uploaded.append(r.json())
    image, video = uploaded
    restored = client.get(root + f"/workbench/references/{video['id']}")
    assert restored.status_code == 200 and restored.json()["kind"] == "video"
    listing = client.get(root + "/workbench/references?media_type=all").json()["items"]
    assert {x["kind"] for x in listing} == {"image", "video"}
    assert {x["kind"] for x in client.get(root + "/workbench/references").json()["items"]} == {
        "image"
    }
    caps = client.get(root + f"/providers/{model['provider_name']}/capabilities").json()
    assert caps["max_reference_videos"] == 1 and "video_reference" in caps["features"]
    calls = []

    def request(provider, method, path, **kwargs):
        assert method == "POST" and path == "/videos"
        calls.append(kwargs["json"])
        return {"id": "upstream-mixed-test"}

    monkeypatch.setattr(VideoV1Provider, "_request", request)
    monkeypatch.setattr(
        VideoV1Provider,
        "poll",
        lambda *args: GenerationResult(
            status="succeeded", outputs=[Output(type="video", data=changing_video)]
        ),
    )
    payload = dict(
        request_key=str(uuid.uuid4()),
        provider=model["provider_name"],
        request_type="video",
        prompt_override="Use the image character and video motion",
        provider_params={"duration": 4, "resolution": "720p"},
        references=[{"generation_id": x["id"], "role": "reference"} for x in uploaded],
    )
    r = client.post(root + "/workbench/estimate", json=payload)
    assert r.status_code == 200, r.text
    r = client.post(root + "/workbench/generate", json=payload)
    assert r.status_code == 200 and r.json()["status"] == "succeeded", r.text
    assert len(calls) == 1 and calls[0]["operation"] == "video_to_video"
    assert [x["kind"] for x in calls[0]["media_inputs"]] == ["image", "video"]
    for material in calls[0]["media_inputs"]:
        result = client.get(material["url"])
        assert result.status_code == 200
        if material["kind"] == "video":
            assert result.content == changing_video
    snapshot = r.json()["input_snapshot"]
    assert [x["kind"] for x in snapshot["references"]] == ["image", "video"]
    assert snapshot["reference_sources"] == payload["references"]
    with sessions() as db:
        saved = db.get(GenerationJob, uuid.UUID(r.json()["id"]))
        assert "base64" not in str(saved.input_snapshot)
    # Video-only references select the same operation; images alone use image_to_video.
    for source, expected in [(video, "video_to_video"), (image, "image_to_video")]:
        next_payload = {
            **payload,
            "request_key": str(uuid.uuid4()),
            "references": [{"generation_id": source["id"], "role": "reference"}],
        }
        result = client.post(root + "/workbench/generate", json=next_payload)
        assert result.status_code == 200 and result.json()["status"] == "succeeded", result.text
        assert calls[-1]["operation"] == expected
    # Reject duplicate and invalid roles, then a foreign-project source, before any submit.
    count = len(calls)
    for references in [
        [payload["references"][1]] * 2,
        [{"generation_id": video["id"], "role": "first_frame"}],
    ]:
        assert (
            client.post(
                root + "/workbench/generate",
                json={**payload, "request_key": str(uuid.uuid4()), "references": references},
            ).status_code
            == 422
        )
    with sessions() as db:
        db.get(Generation, uuid.UUID(video["id"])).project_id = ids["other"]
        db.commit()
    assert (
        client.post(
            root + "/workbench/generate", json={**payload, "request_key": str(uuid.uuid4())}
        ).status_code
        == 404
    )
    assert len(calls) == count
    assert client.get(root + f"/workbench/references/{video['id']}").status_code == 404


def test_video_capability_is_explicit_and_images_do_not_consume_video_limit():
    from app.adapters.contracts import Capabilities, GenerationRequest, ReferenceImage
    from app.modules.generation.channels import ModelIn
    from app.modules.generation.model_catalog import mismatch

    req = GenerationRequest(
        request_type="video",
        prompt="motion",
        references=[
            ReferenceImage(blob_hash="image"),
            ReferenceImage(blob_hash="video", kind="video"),
        ],
    )
    caps = Capabilities(modalities={"video"}, features={"reference"}, max_reference_images=1)
    assert "参考视频" in mismatch(req, caps)
    caps.features.add("video_reference")
    caps.max_reference_videos = 1
    assert mismatch(req, caps) == ""
    req.references.append(ReferenceImage(blob_hash="video2", kind="video"))
    assert "参考视频" in mismatch(req, caps)
    with pytest.raises(ValueError, match="参考视频"):
        ModelIn(
            name="image",
            model="image",
            protocol="gpt_image",
            modality="image",
            max_reference_videos=1,
        )
