"""Conversation attachments reach model inputs; no paid providers are contacted."""

# ruff: noqa: F811
import base64
import io
import json
import uuid
import wave

import pytest
from PIL import Image

from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.attachment import Attachment
from app.models.harness import ProductionRun
from app.modules.attachments import service as attachments
from app.modules.harness import attachment_context, kernel, runtime, service
from app.modules.harness.context import build_context
from app.modules.harness.kernel_modalities import InputCapabilities
from app.modules.harness.media_context import inspect_media
from app.storage import cas
from tests.test_harness_context import context
from tests.test_harness_runtime import seeded
from tests.test_media_workflow import changing_video, studio  # noqa: F401


def picture(color="red"):
    output = io.BytesIO()
    Image.new("RGB", (80, 40), color).save(output, "PNG")
    return output.getvalue()


def sound(seconds=20):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * (16000 * seconds))
    return output.getvalue()


def seed(db, ids, data=None, kind="image", **metadata):
    data = data if data is not None else picture()
    mime = {"image": "image/png", "video": "video/mp4", "audio": "audio/wav"}[kind]
    blob = cas.put_bytes(db, data, mime, **{k: v for k, v in metadata.items() if k != "has_audio"})
    row = Attachment(
        project_id=uuid.UUID(ids["project"]),
        created_by=ids["user"],
        request_key=uuid.uuid4(),
        title="上传参考",
        filename="reference." + mime.split("/")[1],
        kind=kind,
        fingerprint=blob.hash,
        size_bytes=len(data),
        uploaded_bytes=len(data),
        status="ready",
        blob_hash=blob.hash,
        **metadata,
    )
    db.add(row)
    db.flush()
    return attachments.resolve(db, context(db, ids), row.id)


def vision(count=4):
    return InputCapabilities(modalities=["text", "image"], max_images=count).model_dump()


def descriptor(**kw):
    return {
        "source_type": "attachment",
        "id": str(uuid.uuid4()),
        "blob_hash": "a" * 64,
        "kind": "image",
        "title": "仅名称不能证明看过",
        **kw,
    }


def test_descriptors_require_identity_and_fixed_hash_and_ignore_urls():
    item = descriptor(url="https://private.invalid/file")
    ref = attachment_context.current_references([item])[0]
    assert ref["kind"] == "attachment" and ref["expected_blob_hash"] == "a" * 64
    assert "url" not in ref
    with pytest.raises(CapabilityUnsupported, match="固定文件"):
        attachment_context.current_references([descriptor(blob_hash=None)])


def test_current_references_are_scoped_and_deduplicated():
    first, second = descriptor(), descriptor()
    ref = attachment_context.current_references([first])[0]
    assert attachment_context.current_references([first, first, second], available=[ref]) == [ref]
    assert attachment_context.current_references([first], available=[]) == []
    with pytest.raises(CapabilityUnsupported, match="过多"):
        attachment_context.current_references([first] * 17)


def test_history_keeps_only_fixed_locators_not_image_bytes_or_arbitrary_fields():
    item = descriptor(content_parts=[{"type": "image_url", "url": "secret"}], api_key="secret")
    history = attachment_context.history_attachments([item])
    assert history[0]["reference"]["id"] == item["id"]
    assert "not_loaded" in history[0]["observation"]
    assert "secret" not in json.dumps(history)


def test_current_image_model_input_contains_real_decoded_pixels(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        item = seed(db, ids)
        refs = attachment_context.current_references([item])
        parts = attachment_context.current_media_parts(db, ctx, refs, vision())
        evidence = json.loads(parts[0]["text"])
        assert evidence["reference"]["expected_blob_hash"] == item["blob_hash"]
        assert evidence["observation"] == "decoded_media_supplied"
        data = base64.b64decode(parts[1]["image_url"]["url"].split(",", 1)[1])
        image = Image.open(io.BytesIO(data))
        assert image.size == (80, 40) and image.getpixel((30, 20))[0] > 240
        with pytest.raises(CapabilityUnsupported, match="图片理解"):
            attachment_context.current_media_parts(db, ctx, refs, InputCapabilities().model_dump())
        # No switch to a different blob when resuming an old reference.
        with pytest.raises(Conflict, match="固定引用"):
            inspect_media(db, ctx, {**refs[0], "expected_blob_hash": "f" * 64})


def test_attachment_inspection_rechecks_owner_and_membership(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        item = seed(db, ids)
        ref = attachment_context.current_references([item])[0]
        row = db.get(Attachment, uuid.UUID(item["id"]))
        row.created_by = uuid.uuid4()
        db.flush()
        with pytest.raises(NotFound):
            inspect_media(db, ctx, ref)
        row.created_by = ids["user"]
        db.delete(ctx.membership)
        db.flush()
        from app.core.errors import Forbidden

        with pytest.raises(Forbidden):
            inspect_media(db, ctx, ref)


def test_video_samples_two_frames_with_truthful_coverage(studio, changing_video):
    _, sessions, ids = studio
    with sessions() as db:
        item = seed(db, ids, changing_video, "video", duration_ms=2000, width=320, height=180)
        parts = attachment_context.current_media_parts(
            db, context(db, ids), attachment_context.current_references([item]), vision()
        )
        evidence = json.loads(parts[0]["text"])
        assert len(evidence["coverage"]["frames_ms"]) == 2
        assert all(0 <= t < 2000 for t in evidence["coverage"]["frames_ms"])
        assert "motion_between_sampled_frames" in evidence["unobserved"]
        assert "audio_content" in evidence["unobserved"]
        images = [part for part in parts if part["type"] == "image_url"]
        assert len(images) == 2
        # Frames straddle the color change, verifying real decode rather than metadata.
        colors = [
            Image.open(io.BytesIO(base64.b64decode(p["image_url"]["url"].split(",")[1]))).getpixel(
                (100, 80)
            )
            for p in images
        ]
        assert colors[0][0] > 220 and colors[1][2] > 220


def test_audio_has_bounded_real_sample_or_explicit_not_heard(studio):
    _, sessions, ids = studio
    with sessions() as db:
        item = seed(db, ids, sound(), "audio", duration_ms=20000, has_audio=True)
        refs = attachment_context.current_references([item])
        contract = InputCapabilities(
            modalities=["text", "audio"], max_audio_clips=1, max_audio_seconds=30
        ).model_dump()
        parts = attachment_context.current_media_parts(db, context(db, ids), refs, contract)
        assert json.loads(parts[0]["text"])["coverage"]["audio_ranges"] == [
            {"start_ms": 0, "end_ms": 15000}
        ]
        with wave.open(io.BytesIO(base64.b64decode(parts[1]["input_audio"]["data"]))) as audio:
            assert audio.getnframes() / audio.getframerate() == 15
        parts = attachment_context.current_media_parts(
            db, context(db, ids), refs, InputCapabilities().model_dump()
        )
        assert len(parts) == 1 and "未听取" in parts[0]["text"]
        assert json.loads(parts[0]["text"])["coverage"]["audio_ranges"] == []


def test_combined_model_budget_is_enforced(studio):
    _, sessions, ids = studio
    with sessions() as db:
        first, second = seed(db, ids), seed(db, ids, picture("blue"))
        refs = attachment_context.current_references([first, second])
        with pytest.raises(CapabilityUnsupported, match="数量不足"):
            attachment_context.current_media_parts(db, context(db, ids), refs, vision(1))
        with pytest.raises(CapabilityUnsupported, match="数量或大小"):
            attachment_context.current_media_parts(
                db, context(db, ids), refs, {**vision(), "max_media_bytes": 1024}
            )


def test_history_attachments_are_discoverable_without_automatic_loading(studio, monkeypatch):
    _, sessions, ids = studio
    with sessions() as db:
        item = seed(db, ids)
        monkeypatch.setattr(cas, "read_bytes", lambda *_: pytest.fail("历史不得自动读取文件"))
        packet = build_context(
            db,
            context(db, ids),
            goal="继续聊天",
            history=[
                {
                    "id": str(uuid.uuid4()),
                    "role": "user",
                    "content": "刚才的图",
                    "attachments": [item],
                }
            ],
        )
        historical = packet["history"][0]["attachments"][0]
        assert historical["reference"]["id"] == item["id"]
        assert "not_loaded" in historical["observation"]


def test_harness_dispatches_current_image_to_kernel_without_persisting_bytes(studio, monkeypatch):
    def decide(config, packet, parts):
        assert any(part["type"] == "image_url" for part in parts)
        assert packet["context"]["references"][0]["reference"]["kind"] == "attachment"
        return {"kind": "finish", "message": "已收到图片内容", "artifacts": []}

    decide.input_capabilities = vision()
    monkeypatch.setitem(kernel._KERNELS, "fixture", decide)
    _, sessions, ids = studio
    with sessions() as db:
        item = seed(db, ids)
        db.commit()
    sessions, _, rid = seeded(
        studio,
        initial_context={
            "current_attachments": [item],
            "references": attachment_context.current_references([item]),
        },
    )
    with sessions() as db:
        ticket = service.claim(db, rid)
        db.commit()
    runtime.execute(ticket, sessions=sessions)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        assert run.status == "succeeded", run.error
        assert "base64" not in json.dumps(run.context)
        for task in service.tasks(db, run):
            assert "base64" not in json.dumps(task.state)


def test_legacy_model_request_preserves_multimodal_content(monkeypatch):
    from app.modules.agent import worker

    observed = {}

    def chat(system, user, *args, **kw):
        observed.update(user=user, provider=kw["sdk_provider"])
        return '{"tool":"finish","message":"收到","arguments":{}}'

    monkeypatch.setattr(worker, "_chat", chat)
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture"}}
    action = worker.propose(
        {
            "engine": "cloud_llm",
            "base_url": "https://fixture.invalid",
            "api_key": "fixture",
            "model": "fixture",
            "sdk_provider": "openai",
        },
        {"goal": "看图"},
        [image],
    )
    assert action.tool == "finish" and observed["user"][1] == image
    assert observed["provider"] == "openai"


def test_legacy_worker_decodes_current_attachment_before_decision(studio, monkeypatch):
    from app.models.agent import AgentRun
    from app.modules.agent import worker
    from app.modules.agent.schemas import ModelAction
    from app.modules.harness import kernel_modalities
    from tests.test_agent import setup

    _, sessions, ids, _, run = setup(studio, monkeypatch)
    with sessions() as db:
        item = seed(db, ids)
        row = db.get(AgentRun, uuid.UUID(run["id"]))
        row.engine = "cloud_llm"
        row.input_context = {"current_attachments": [item]}
        db.commit()
    monkeypatch.setattr(
        kernel_modalities,
        "resolve_cloud_input",
        lambda *a, **kw: {"config": {}, "input_contract": vision()},
    )

    def decide(config, payload, parts):
        assert payload["conversation_context"]["current_attachments"][0]["id"] == item["id"]
        assert any(part["type"] == "image_url" for part in parts)
        return ModelAction(tool="finish", message="看到了图片")

    monkeypatch.setattr(worker, "propose", decide)
    worker.advance(uuid.UUID(run["id"]))
    with sessions() as db:
        row = db.get(AgentRun, uuid.UUID(run["id"]))
        assert row.status == "succeeded", row.error
        assert "base64" not in json.dumps(row.input_context)
