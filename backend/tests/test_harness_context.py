"""Context semantics plus real, local media decode in an isolated project schema."""

# ruff: noqa: F811
import base64
import io
import uuid
import wave

import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select

from app.core.deps import ProjectContext
from app.core.errors import CapabilityUnsupported, Conflict, Forbidden, NotFound
from app.models.identity import Membership, Project, User
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaAnnotation
from app.modules.harness.context import ContextReference, build_context, effective_facts
from app.modules.harness.media_context import inspect_media
from app.storage import cas
from tests.test_media_workflow import changing_video, studio  # noqa: F401


def fact(fid, text, sequence, **kw):
    return {
        "id": fid,
        "text": text,
        "sequence": sequence,
        "kind": "user_decision",
        "source_id": f"message:{sequence}",
        **kw,
    }


def test_replaced_then_withdrawn_decision_does_not_resurrect():
    records = [
        fact("red", "红衣", 1, key="costume"),
        fact("black", "黑衣", 2, key="costume", status="withdrawn"),
        fact("summary", "主角红衣", 3, kind="summary", depends_on=["red"]),
        fact("summary2", "继续红衣", 4, kind="summary", depends_on=["summary"]),
    ]
    active, omitted = effective_facts(records)
    assert not active
    assert len(omitted) == 4


def test_inference_does_not_replace_user_decision_and_scopes_stay_separate():
    records = [
        fact("global", "黑衣", 1, key="costume"),
        fact("scene", "本场红衣", 2, key="costume", scope="scene:1"),
        fact("guess", "可能是蓝衣", 3, kind="inference", key="costume"),
    ]
    active, _ = effective_facts(records, scopes=["scene:2"])
    assert {f.id for f in active} == {"global", "guess"}
    with pytest.raises(Conflict):
        effective_facts(
            [records[0], fact("guess", "蓝衣", 3, kind="inference", supersedes=["global"])]
        )


def test_missing_and_cyclic_summary_dependencies_are_not_usable():
    records = [
        fact("a", "一", 1, kind="summary", depends_on=["b"]),
        fact("b", "二", 2, kind="summary", depends_on=["a"]),
        fact("c", "三", 3, kind="summary", depends_on=["missing"]),
        fact("d", "四", 4, kind="summary"),
    ]
    assert effective_facts(records)[0] == []


@pytest.mark.parametrize(
    "extra",
    [
        {"revision": 1},
        {"start_ms": 0},
        {"start_ms": 100, "end_ms": 99},
        {"url": "https://example.com/image.png"},
        {"region": {"x": 0.8, "y": 0, "width": 0.5, "height": 1}},
    ],
)
def test_reference_rejects_floating_or_invalid_media_selectors(extra):
    with pytest.raises(ValidationError):
        ContextReference(kind="media_version", id=uuid.uuid4(), **extra)


def test_document_reference_requires_fixed_work_and_revision():
    with pytest.raises(ValidationError):
        ContextReference(kind="document", id=uuid.uuid4())


def context(db, ids):
    project = db.get(Project, uuid.UUID(ids["project"]))
    user = db.get(User, ids["user"])
    member = db.scalar(
        select(Membership).where(Membership.project_id == project.id, Membership.user_id == user.id)
    )
    return ProjectContext(project, member, user)


def seed(db, ids, data, typ, mime, **metadata):
    blob = cas.put_bytes(db, data, mime, **metadata)
    item = LibraryMedia(project_id=uuid.UUID(ids["project"]), name="Context source")
    db.add(item)
    db.flush()
    version = MediaVersion(
        media_id=item.id,
        ordinal=1,
        media_type=typ,
        filename="source",
        fingerprint=blob.hash,
        size_bytes=len(data),
        uploaded_bytes=len(data),
        status="ready",
        original_hash=blob.hash,
        **metadata,
    )
    db.add(version)
    db.flush()
    return {"kind": "media_version", "id": str(version.id)}


def test_packet_budget_fixed_metadata_and_revoked_membership(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        buffer = io.BytesIO()
        Image.new("RGB", (100, 50), "red").save(buffer, "PNG")
        reference = seed(db, ids, buffer.getvalue(), "image", "image/png", width=100, height=50)
        first = build_context(
            db,
            ctx,
            goal="只改眼神",
            references=[reference],
            facts=[fact("old", "红衣", 1, key="costume"), fact("new", "黑衣", 2, key="costume")],
        )
        assert first["references"][0]["observation"] == "metadata_only"
        assert first["references"][0]["reference"]["expected_blob_hash"]
        assert [f["id"] for f in first["facts"]["user_decision"]] == ["new"]
        repeated = build_context(
            db,
            ctx,
            goal="只改眼神",
            references=[reference],
            facts=[fact("old", "红衣", 1, key="costume"), fact("new", "黑衣", 2, key="costume")],
        )
        assert first["content_hash"] == repeated["content_hash"]
        assert first["budget"]["used"] <= first["budget"]["limit"]
        with pytest.raises(CapabilityUnsupported, match="已确认要求"):
            build_context(
                db,
                ctx,
                goal="修改",
                facts=[fact("must", "长" * 1500, 1)],
                budget={"max_chars": 1000},
            )
        packet = build_context(
            db,
            ctx,
            goal="修改",
            budget={"max_chars": 1000},
            facts=[fact("optional", "长" * 1500, 1, kind="inference")],
        )
        assert packet["omitted"] == [{"id": "optional", "reason": "context_budget"}]
        packet = build_context(
            db,
            ctx,
            goal="修改",
            references=[{**reference, "required": True}],
            facts=[fact("large", "长" * 1800, 1, kind="observation")],
            budget={"max_chars": 2200},
        )
        assert len(packet["references"]) == 1
        assert packet["facts"]["observation"] == []
        db.delete(ctx.membership)
        db.flush()
        with pytest.raises(Forbidden):
            build_context(db, ctx, goal="继续", references=[reference])


def test_actual_image_crop_and_cross_project_denial(studio):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        im = Image.new("RGB", (80, 40), "red")
        im.paste("blue", (40, 0, 80, 40))
        out = io.BytesIO()
        im.save(out, "PNG")
        reference = seed(db, ids, out.getvalue(), "image", "image/png", width=80, height=40)
        result = inspect_media(
            db, ctx, {**reference, "region": {"x": 0.5, "y": 0, "width": 0.5, "height": 1}}
        )
        actual = Image.open(
            io.BytesIO(
                base64.b64decode(result["content_parts"][0]["image_url"]["url"].split(",", 1)[1])
            )
        )
        assert actual.size == (40, 40)
        red, _, blue = actual.getpixel((20, 20))
        assert blue > 240 and red < 10
        assert "model_interpretation" in result["unobserved"]
        with pytest.raises(Conflict):
            inspect_media(db, ctx, {**reference, "expected_blob_hash": "a" * 64})
        # Even a membership in the other project must not expose this project data.
        member = Membership(project_id=ids["other"], user_id=ctx.user.id, role="admin")
        db.add(member)
        db.flush()
        foreign = ProjectContext(db.get(Project, ids["other"]), member, ctx.user)
        with pytest.raises(NotFound):
            inspect_media(db, foreign, reference)


def test_video_frames_audio_and_transcript_have_explicit_coverage(studio, changing_video):
    _, sessions, ids = studio
    with sessions() as db:
        ctx = context(db, ids)
        reference = seed(db, ids, changing_video, "video", "video/mp4", duration_ms=2000)
        db.add(
            MediaAnnotation(
                version_id=uuid.UUID(reference["id"]),
                start_ms=1100,
                end_ms=1800,
                kind="speech",
                text="你好",
                source={"method": "test"},
                created_by=ctx.user.id,
            )
        )
        db.flush()
        result = inspect_media(
            db,
            ctx,
            {**reference, "start_ms": 1000, "end_ms": 1900},
            frame_times_ms=[1200, 1700],
            include_audio=True,
        )
        assert result["coverage"]["frames_ms"] == [1200, 1700]
        assert result["coverage"]["audio_ranges"] == [{"start_ms": 1000, "end_ms": 1900}]
        assert result["transcript"][0]["text"] == "你好"
        assert result["transcript"][0]["word_alignment_verified"] is False
        assert "motion_between_sampled_frames" in result["unobserved"]
        image = next(p for p in result["content_parts"] if p["type"] == "image_url")
        frame = Image.open(io.BytesIO(base64.b64decode(image["image_url"]["url"].split(",", 1)[1])))
        red, _, blue = frame.getpixel((160, 90))
        assert blue > 200 and red < 20
        audio = next(p for p in result["content_parts"] if p["type"] == "input_audio")
        with wave.open(io.BytesIO(base64.b64decode(audio["input_audio"]["data"]))) as wav:
            assert wav.getframerate() == 16000
            assert 0.85 < wav.getnframes() / wav.getframerate() < 0.95
        with pytest.raises(CapabilityUnsupported):
            inspect_media(
                db, ctx, {**reference, "start_ms": 1000, "end_ms": 1900}, frame_times_ms=[200]
            )
        with pytest.raises(CapabilityUnsupported):
            inspect_media(db, ctx, reference, frame_times_ms=[0, 100, 200, 300, 400])


def test_text_only_audio_context_does_not_claim_listening(studio):
    _, sessions, ids = studio
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000)
    with sessions() as db:
        ctx = context(db, ids)
        reference = seed(db, ids, stream.getvalue(), "audio", "audio/wav", duration_ms=1000)
        result = inspect_media(db, ctx, reference)
        assert result["content_parts"] == []
        assert "audio_content" in result["unobserved"]
        assert result["transcript_status"].startswith("no_existing_transcript")


def test_engineering_reference_reads_fixed_revision_after_editor_changes(studio):
    from tests.test_engineering_review import ready
    from tests.test_media_workflow import post_ok

    client, sessions, ids, path, tid, url = ready(studio)
    root = path.removesuffix("/engineering")
    post_ok(client, root + "/timelines", {"timeline_id": tid})
    docs = client.get(path + "/documents").json()["documents"]
    seq = next(d for d in docs if d["kind"] == "sequence.timeline")
    reference = {
        "kind": "document",
        "id": seq["id"],
        "revision": seq["working_revision"],
        "work_id": root.split("/")[-1],
    }
    with sessions() as db:
        before = build_context(db, context(db, ids), goal="继续剪辑", references=[reference])
    content = client.get(url).json()
    content["items"][0]["duration_ms"] = 700
    response = client.put(url, json=content)
    assert response.status_code == 200, response.text
    with sessions() as db:
        after = build_context(db, context(db, ids), goal="继续剪辑", references=[reference])
    assert after["content_hash"] == before["content_hash"]
    assert after["references"][0]["payload"]["editor"]["items"][0]["duration_ms"] != 700
