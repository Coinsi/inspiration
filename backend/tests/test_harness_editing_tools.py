"""Editable candidates and real local rendering, without a paid provider or AgentRun."""

# ruff: noqa: F811
import array
import copy
import io
import math
import uuid
import wave
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.models.agent import AgentRun
from app.models.engineering import EngineeringChange, EngineeringDocumentRevision
from app.models.enums import Role
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionRun
from app.models.identity import Membership, Project, User
from app.models.library import LibraryMedia, MediaVersion
from app.models.storage import Blob
from app.models.timeline import AudioTrack, Timeline, TimelineItem
from app.models.work import CreativeWork
from app.modules.engineering import documents
from app.modules.generation import media_engine, video_tools
from app.modules.harness import editing_tools, media_tools
from app.modules.tools import capabilities, registry
from app.modules.tools.contracts import ToolContext
from app.modules.tools.invocation import invoke
from app.storage import cas
from tests.test_media_workflow import post_ok, studio  # noqa: F401


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    editing_tools.register_tools()
    media_tools.register_tools()


def context(work_id, **grant):
    return ToolContext(
        work_id=uuid.UUID(str(work_id)),
        input_context={
            "execution": {"request_key": str(uuid.uuid4()), **grant},
        },
    )


def ctx(db, ids):
    return SimpleNamespace(
        project=db.get(Project, uuid.UUID(ids["project"])), user=db.get(User, ids["user"])
    )


def work(studio):
    client, _, ids = studio
    return post_ok(client, f"/api/v1/projects/{ids['project']}/works", {"title": "可编辑制作"})[
        "id"
    ]


def png(color, size=(64, 64)):
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, "PNG")
    return output.getvalue()


def sound(seconds=2):
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(
            array.array(
                "h",
                [
                    int(12000 * math.sin(2 * math.pi * 440 * i / 48000))
                    for i in range(seconds * 48000)
                ],
            ).tobytes()
        )
    return out.getvalue()


def source(sessions, ids, raw, kind="image", *, library=False, duration_ms=None):
    mime = {"image": "image/png", "audio": "audio/wav", "video": "video/mp4"}[kind]
    with sessions() as db:
        blob = cas.put_bytes(db, raw, mime, duration_ms=duration_ms)
        if library:
            item = LibraryMedia(project_id=uuid.UUID(ids["project"]), name="固定音频")
            db.add(item)
            db.flush()
            row = MediaVersion(
                media_id=item.id,
                ordinal=1,
                filename="source.wav",
                media_type=kind,
                fingerprint=blob.hash,
                size_bytes=blob.size_bytes,
                uploaded_bytes=blob.size_bytes,
                status="ready",
                original_hash=blob.hash,
                duration_ms=duration_ms,
            )
        else:
            job = GenerationJob(
                project_id=uuid.UUID(ids["project"]),
                created_by=ids["user"],
                target_type="shot",
                target_id=uuid.UUID(ids["shot"]),
                provider="local",
                request_type=kind,
                status="succeeded",
                estimated_cost=0,
                actual_cost=0,
                input_snapshot={},
            )
            db.add(job)
            db.flush()
            row = Generation(
                project_id=job.project_id,
                job_id=job.id,
                target_type="shot",
                target_id=job.target_id,
                provider="local",
                output_type=kind,
                output_blob_hash=blob.hash,
                cost_points=0,
                input_refs={},
            )
        db.add(row)
        db.flush()
        result = {
            "kind": "media_version" if library else "generation",
            "id": str(row.id),
            "expected_blob_hash": blob.hash,
        }
        db.commit()
        return result


def render(db, ids, task, candidate):
    job = invoke(db, ctx(db, ids), task, "engineering.preview", candidate["preview_arguments"])
    assert job.status == "succeeded", job.model_dump()
    result = invoke(
        db,
        ctx(db, ids),
        task,
        "engineering.preview_status",
        {
            "work_id": candidate["work_id"],
            "change_id": candidate["change_id"],
            "output_id": candidate["output_id"],
        },
    )
    assert result.status == "succeeded", result.model_dump()
    assert result.data["status"] == "succeeded", result.data
    assert result.data["materialized"] and not result.data["adopted"]
    return db.get(Blob, result.data["outputs"][0]["blob_hash"])


def test_image_recipe_pins_layers_mask_and_persists_candidate_without_adoption(studio):
    _, sessions, ids = studio
    wid = work(studio)
    red = source(sessions, ids, png("red", (4, 4)))
    mask = source(sessions, ids, png((128, 128, 128, 255), (4, 4)))
    task = context(wid)
    args = {
        "work_id": wid,
        "base_revision": 0,
        "title": "蒙版图层",
        "recipe": {
            "mode": "image",
            "width": 8,
            "height": 8,
            "layers": [
                {"name": "主角", "source": red, "mask": mask, "x": 2, "y": 1, "opacity": 0.5}
            ],
        },
    }
    with sessions() as db:
        result = invoke(db, ctx(db, ids), task, "engineering.compose", args)
        assert result.status == "succeeded", result.model_dump()
        candidate = result.data
        assert not candidate["rendered"] and not candidate["adopted"]
        assert candidate["document_kind"] == "image.document"
        graph = documents.closure(db, uuid.UUID(wid), candidate["document_ref"])
        assert {n["payload"]["blob_hash"] for n in graph if n["kind"] == "resource"} == {
            red["expected_blob_hash"],
            mask["expected_blob_hash"],
        }
        repeated = invoke(db, ctx(db, ids), task, "engineering.compose", args)
        assert repeated.data["change_id"] == candidate["change_id"]
        different = invoke(db, ctx(db, ids), task, "engineering.compose", {**args, "title": "不同"})
        assert different.status == "failed" and different.error.code == "VERSION_CONFLICT"
        assert db.scalar(select(func.count()).select_from(EngineeringChange)) == 1
        blob = render(db, ids, task, candidate)
        pixels = Image.open(io.BytesIO(cas.read_bytes(blob))).convert("RGBA")
        assert pixels.size == (8, 8)
        assert pixels.getpixel((0, 0)) == (0, 0, 0, 0)
        assert pixels.getpixel((2, 1)) == (255, 0, 0, 64)
        assert db.get(CreativeWork, uuid.UUID(wid)).engineering_revision == 0
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_native_audio_trims_gains_and_positions_real_samples(studio):
    _, sessions, ids = studio
    wid = work(studio)
    audio = source(sessions, ids, sound(), "audio", duration_ms=2000)
    task = context(wid)
    args = {
        "work_id": wid,
        "base_revision": 0,
        "title": "对白剪辑",
        "recipe": {
            "mode": "audio",
            "channels": 1,
            "tracks": [
                {
                    "name": "对白",
                    "clips": [
                        {
                            "source": audio,
                            "source_start_ms": 400,
                            "start_ms": 200,
                            "duration_ms": 500,
                            "gain_db": -6.0206,
                        }
                    ],
                }
            ],
        },
    }
    with sessions() as db:
        result = invoke(db, ctx(db, ids), task, "engineering.compose", args)
        assert result.status == "succeeded", result.model_dump()
        candidate = result.data
        revision = db.get(
            EngineeringDocumentRevision,
            (
                uuid.UUID(candidate["document_ref"]["id"]),
                candidate["document_ref"]["revision"],
            ),
        )
        assert revision.schema_version == 2
        assert revision.payload["tracks"][0]["clips"][0]["source_map"]["audio_policy"] == "resample"
        blob = render(db, ids, task, candidate)
        with wave.open(io.BytesIO(cas.read_bytes(blob))) as audiofile:
            assert audiofile.getframerate() == 48000 and audiofile.getnframes() == 33600
            samples = array.array("h", audiofile.readframes(audiofile.getnframes()))
        assert max(abs(v) for v in samples[:9600]) == 0
        assert 5990 <= max(abs(v) for v in samples[9600:]) <= 6010
        assert db.get(CreativeWork, uuid.UUID(wid)).engineering_revision == 0


def test_sequence_combines_video_image_library_audio_fades_overlay_and_is_editable(
    studio, tmp_path
):
    client, sessions, ids = studio
    wid = work(studio)
    video = tmp_path / "source.mp4"
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x90:r=24:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ]
    )
    moving = source(sessions, ids, video.read_bytes(), "video", duration_ms=1000)
    green = source(sessions, ids, png("lime", (160, 90)))
    blue = source(sessions, ids, png("blue"))
    music = source(sessions, ids, sound(), "audio", library=True, duration_ms=2000)
    task = context(wid)
    args = {
        "work_id": wid,
        "base_revision": 0,
        "title": "组合剪辑",
        "recipe": {
            "mode": "sequence",
            "clips": [
                {"source": moving, "duration_ms": 1000},
                {"source": green, "duration_ms": 1000},
            ],
            "audio": [
                {
                    "source": music,
                    "name": "配乐",
                    "duration_ms": 2000,
                    "gain_db": -6,
                    "fade_in_ms": 500,
                    "fade_out_ms": 500,
                }
            ],
            "overlays": [
                {
                    "source": blue,
                    "duration_ms": 2000,
                    "x": 0.7,
                    "y": 0.7,
                    "width": 0.2,
                    "height": 0.2,
                    "fit": "cover",
                }
            ],
        },
    }
    with sessions() as db:
        result = invoke(db, ctx(db, ids), task, "engineering.compose", args)
        assert result.status == "succeeded", result.model_dump()
        candidate = result.data
        assert candidate["document_kind"] == "sequence.timeline"
        repeated = invoke(db, ctx(db, ids), task, "engineering.compose", args)
        assert repeated.status == "succeeded" and repeated.data == candidate
        assert db.scalar(select(func.count()).select_from(Timeline)) == 1
        assert db.scalar(select(func.count()).select_from(TimelineItem)) == 0
        assert db.scalar(select(func.count()).select_from(AudioTrack)) == 0
        blob = render(db, ids, task, candidate)
        out = tmp_path / "preview.mp4"
        out.write_bytes(cas.read_bytes(blob))
        metadata = video_tools.probe_file(out)
        assert metadata["has_video"] and metadata["has_audio"]
        assert abs(metadata["duration_ms"] - 2000) < 100
        for instant, channel in (("0.5", 0), ("1.5", 1)):
            frame = tmp_path / f"frame-{instant}.png"
            media_engine.run(["-ss", instant, "-i", str(out), "-frames:v", "1", str(frame)])
            pixels = Image.open(frame).convert("RGB")
            assert pixels.getpixel((100, 100))[channel] > 220
            overlay_pixel = pixels.getpixel((int(pixels.width * 0.8), int(pixels.height * 0.8)))
            assert overlay_pixel[2] > 220 and overlay_pixel[0] < 30
        wav = tmp_path / "preview.wav"
        media_engine.run(["-i", str(out), "-vn", "-ar", "48000", "-ac", "1", str(wav)])
        with wave.open(str(wav)) as soundfile:
            samples = array.array("h", soundfile.readframes(soundfile.getnframes()))

        def rms(a, b):
            values = samples[int(a * 48000) : int(b * 48000)]
            return math.sqrt(sum(v * v for v in values) / len(values))

        assert rms(0.01, 0.1) < rms(0.7, 0.9) * 0.3
        assert rms(1.9, 1.99) < rms(0.7, 0.9) * 0.3
        assert db.scalar(select(func.count()).select_from(TimelineItem)) == 0
        db.commit()
    path = f"/api/v1/projects/{ids['project']}/works/{wid}/engineering"
    post_ok(
        client,
        f"{path}/changes/{candidate['change_id']}/accept",
        {
            "fingerprint": candidate["fingerprint"],
        },
    )
    document = client.get(
        f"/api/v1/projects/{ids['project']}/timelines/{candidate['timeline_id']}/document"
    ).json()
    assert len(document["items"]) == 2 and len(document["audio"]) == 1
    assert document["audio"][0]["fade_in_ms"] == 500
    assert document["visuals"][0]["width"] == 0.2
    assert document["revision"] == 1


@pytest.mark.parametrize("fault", ["overrun", "hash", "effect", "missing_key", "model_key"])
def test_invalid_recipe_is_atomic_and_does_not_leave_partial_timelines(studio, fault):
    _, sessions, ids = studio
    wid = work(studio)
    picture = source(sessions, ids, png("red"))
    audio = source(sessions, ids, sound(), "audio", duration_ms=2000)
    task = context(wid)
    args = {
        "work_id": wid,
        "base_revision": 0,
        "title": "应拒绝",
        "recipe": {
            "mode": "sequence",
            "clips": [{"source": picture, "duration_ms": 2000}],
            "audio": [{"source": audio, "duration_ms": 2000}],
        },
    }
    if fault == "overrun":
        args["recipe"]["audio"][0]["source_start_ms"] = 1
    elif fault == "hash":
        args["recipe"]["clips"][0]["source"]["expected_blob_hash"] = "0" * 64
    elif fault == "effect":
        args["recipe"]["clips"][0]["optical_flow"] = True
    elif fault == "missing_key":
        task = ToolContext(work_id=uuid.UUID(wid))
    elif fault == "model_key":
        args["request_key"] = str(uuid.uuid4())
    with sessions() as db:
        result = invoke(db, ctx(db, ids), task, "engineering.compose", args)
        assert result.status == "failed", result.model_dump()
        assert db.scalar(select(func.count()).select_from(Timeline)) == 0
        assert db.scalar(select(func.count()).select_from(EngineeringChange)) == 0


def test_native_unsupported_effects_rejected_and_catalogue_truthful(studio):
    _, sessions, ids = studio
    wid = work(studio)
    picture = source(sessions, ids, png("red"))
    with sessions() as db:
        args = {
            "work_id": wid,
            "base_revision": 0,
            "title": "未支持缩放",
            "recipe": {
                "mode": "image",
                "width": 64,
                "height": 64,
                "layers": [{"source": picture, "scale": 2}],
            },
        }
        result = invoke(db, ctx(db, ids), context(wid), "engineering.compose", args)
        assert result.status == "failed"
        assert db.scalar(select(func.count()).select_from(EngineeringChange)) == 0
        contract = capabilities.get_capability("engineering.compose")
        assert contract.effect == "candidate"
        assert not contract.policy.charges_on_invoke and not contract.policy.starts_job
        assert "request_key" not in contract.input_schema["properties"]


def test_existing_work_edit_keeps_original_until_review_and_rechecks_permissions(studio):
    client, sessions, ids = studio
    wid = work(studio)
    picture = source(sessions, ids, png("red"))
    args = {
        "work_id": wid,
        "base_revision": 0,
        "title": "图层工程",
        "recipe": {
            "mode": "image",
            "width": 64,
            "height": 64,
            "layers": [{"name": "角色", "source": picture}],
        },
    }
    with sessions() as db:
        result = invoke(db, ctx(db, ids), context(wid), "engineering.compose", args)
        assert result.status == "succeeded", result.model_dump()
        first = result.data
        db.commit()
    path = f"/api/v1/projects/{ids['project']}/works/{wid}/engineering"
    post_ok(
        client, f"{path}/changes/{first['change_id']}/accept", {"fingerprint": first["fingerprint"]}
    )
    with sessions() as db:
        original = documents.read_revision(
            db,
            uuid.UUID(wid),
            uuid.UUID(first["document_ref"]["id"]),
            first["document_ref"]["revision"],
        )[1]
        revised = copy.deepcopy(args)
        revised.update(
            base_revision=1,
            document_id=first["document_ref"]["id"],
            document_revision=first["document_ref"]["revision"],
        )
        revised["recipe"]["layers"][0].update(id=original.payload["layers"][0]["id"], x=10)
        result = invoke(db, ctx(db, ids), context(wid), "engineering.compose", revised)
        assert result.status == "succeeded", result.model_dump()
        assert result.data["document_ref"]["revision"] == first["document_ref"]["revision"] + 1
        assert original.payload["layers"][0]["transform"]["x"] == 0
        assert db.get(CreativeWork, uuid.UUID(wid)).engineering_revision == 1
        member = db.scalar(select(Membership).where(Membership.user_id == ids["user"]))
        member.role = Role.viewer
        db.commit()
        denied = invoke(db, ctx(db, ids), context(wid), "engineering.compose", revised)
        assert denied.status == "failed" and denied.error.code == "FORBIDDEN"


def test_harness_pause_fences_new_candidate_writes(studio):
    from tests.test_harness_media_tools import context as run_context
    from tests.test_harness_media_tools import setup_run

    _, sessions, ids = studio
    wid = work(studio)
    picture = source(sessions, ids, png("red"))
    run_id, task_ids = setup_run(sessions, ids)
    task = run_context(run_id, task_ids[0])
    task.work_id = uuid.UUID(wid)
    args = {
        "work_id": wid,
        "base_revision": 0,
        "title": "暂停后不能写",
        "recipe": {"mode": "image", "width": 64, "height": 64, "layers": [{"source": picture}]},
    }
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        run.work_id = uuid.UUID(wid)
        run.policy = {"effects": ["candidate"]}
        run.status = "paused"
        db.commit()
        result = invoke(db, ctx(db, ids), task, "engineering.compose", args)
        assert result.status == "failed" and result.error.code == "VERSION_CONFLICT"
        assert db.scalar(select(func.count()).select_from(EngineeringChange)) == 0
