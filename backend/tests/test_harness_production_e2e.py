"""One chat, parallel editable workers, actual render, local feedback and human adoption.

The decision adapter below is deliberately scripted: it tests orchestration,
not creative quality or a paid model. Every tool, candidate, media file, render,
validation, review and conversation update uses the actual application path.
"""

# ruff: noqa: F811
import array
import copy
import json
import math
import os
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock, get_ident

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.core.config import settings
from app.models.agent import AgentRun
from app.models.engineering import EngineeringChange, EngineeringDocumentRevision
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionRun, ProductionTask
from app.models.storage import Blob
from app.models.timeline import AudioTrack, TimelineItem
from app.models.work import CreativeWork
from app.modules.generation import media_engine, video_tools
from app.modules.harness import editing_tools, kernel, media_tools, runtime, service
from app.modules.tools import capabilities, registry
from app.storage import cas
from tests.test_harness_editing_tools import png, sound, source, work
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def fixed(reference):
    return {key: reference[key] for key in ("kind", "id", "expected_blob_hash")}


def receipts(result):
    return result.get("receipts", [])


def output_from(result, kind):
    for receipt in reversed(receipts(result)):
        data = receipt.get("data")
        if isinstance(data, dict) and data.get("technical_validated"):
            for output in data.get("outputs", []):
                if output["kind"] == kind:
                    assert output["technical_validation"]["status"] == "passed"
                    return fixed(output["reference"])
    raise AssertionError(f"No technically verified {kind} output in task receipt")


def candidate_from(task):
    for step in task.state.get("steps", []):
        receipt = step.get("receipt", {})
        if receipt.get("tool") == "engineering.compose" and receipt["status"] == "succeeded":
            return receipt["data"]
    raise AssertionError("No persisted editable candidate")


class ScriptedProduction:
    """Decisions only; cannot read DB or forge handler results."""

    def __init__(self, picture, music):
        self.picture, self.music = picture, music
        self.barrier = Barrier(2)
        self.lock = Lock()
        self.parallel_threads = set()
        self.preserved_seen = None

    def plan(self, preserved=None):
        tasks = []
        if preserved is None:
            tasks.append(
                {
                    "key": "picture",
                    "role": "picture",
                    "goal": "制作可编辑画面图层",
                    "tools": ["engineering.read", "engineering.compose", "engineering.preview"],
                    "expected_output": "artifact",
                    "references": [self.picture],
                }
            )
        tasks.append(
            {
                "key": "music_revision" if preserved else "music",
                "role": "music_revision" if preserved else "music",
                "goal": "配乐音量再降低六分贝" if preserved else "制作可编辑配乐音轨",
                "tools": ["engineering.read", "engineering.compose", "engineering.preview"],
                "expected_output": "artifact",
                "references": [self.music],
                "output_requirements": {"duration_ms": 2000, "has_audio": True},
            }
        )
        tasks.append(
            {
                "key": "assemble_revision" if preserved else "assemble",
                "role": "assemble_revision" if preserved else "assemble",
                "goal": "用固定的画面与音轨拼接成片，等待用户采用",
                "tools": ["engineering.read", "engineering.compose", "engineering.preview"],
                "dependencies": ["music_revision"] if preserved else ["picture", "music"],
                "references": [preserved] if preserved else [],
                "expected_output": "artifact",
                "output_requirements": {"duration_ms": 2000, "has_audio": True, "has_video": True},
            }
        )
        return {
            "kind": "plan",
            "plan": {"summary": "离线决策验收：并行画面与配乐，再剪辑审阅。", "tasks": tasks},
        }

    def __call__(self, config, packet, media):
        try:
            return self.decide(config, packet, media)
        except (AssertionError, StopIteration) as exc:
            pytest.fail(f"Scripted decision could not continue {packet['task']['role']}: {exc}")

    def decide(self, config, packet, media):
        assert not config and not media
        task = packet["task"]
        role = task["role"]
        if role == "coordinator":
            if packet["child_results"]:
                result = {
                    "kind": "finish",
                    "message": "离线决策验收完成：实际成片已渲染，采用由用户确认。",
                }
                if task["expected_output"] == "artifact":
                    merged = {
                        "receipts": [
                            r for c in packet["child_results"] for r in receipts(c["result"])
                        ]
                    }
                    kind = "video" if task["output_requirements"].get("has_video") else "audio"
                    result["artifacts"] = [output_from(merged, kind)]
                return result
            if task["key"].startswith("revision_"):
                if task["output_requirements"].get("has_video"):
                    # Each affected dependency boundary now retains its own grant.
                    # Assembly receives the newly completed music boundary and
                    # the unaffected picture as real dependency receipts.
                    combined = {
                        "receipts": [
                            r for d in packet["dependencies"] for r in receipts(d["result"])
                        ]
                    }
                    return {
                        "kind": "delegate",
                        "plan": {
                            "summary": "沿用已批准的剪辑步骤，固定新的配乐和原画面。",
                            "tasks": [
                                {
                                    "key": "assemble_revision",
                                    "role": "assemble_revision",
                                    "goal": "重新合成两秒短片，等用户采用",
                                    "tools": [
                                        "engineering.read",
                                        "engineering.compose",
                                        "engineering.preview",
                                    ],
                                    "expected_output": "artifact",
                                    "references": [
                                        output_from(combined, "image"),
                                        output_from(combined, "audio"),
                                    ],
                                    "output_requirements": task["output_requirements"],
                                }
                            ],
                        },
                    }
                retained = packet.get("preserved_results", [])
                assert retained, "Local replan must receive retained worker results from the system"
                picture = next(item for item in retained if item.get("role") == "picture")
                self.preserved_seen = copy.deepcopy(picture)
                revised = self.plan(output_from(picture["result"], "image"))
                revised["plan"]["tasks"] = revised["plan"]["tasks"][:1]
                return revised
            return self.plan()
        steps = [s["receipt"] for s in packet["steps"]]
        failed = [s for s in steps if s.get("status") == "failed"]
        if failed:
            pytest.fail(f"Actual tool failed in {role}: {failed[-1].get('error')}")
        baseline = next((s["data"] for s in steps if s.get("tool") == "engineering.read"), None)
        if baseline is None:
            return {
                "kind": "tool",
                "tool": "engineering.read",
                "arguments": {"work_id": packet["work_id"], "view": "overview"},
            }
        composed = next((s["data"] for s in steps if s.get("tool") == "engineering.compose"), None)
        if composed is None:
            if role in {"picture", "music"}:
                with self.lock:
                    self.parallel_threads.add(get_ident())
                self.barrier.wait(timeout=15)
            refs = [fixed(ref) for ref in task["references"]]
            if role == "picture":
                recipe = {
                    "mode": "image",
                    "width": 160,
                    "height": 90,
                    "opaque": True,
                    "layers": [{"name": "画面", "source": refs[0]}],
                }
            elif role in {"music", "music_revision"}:
                recipe = {
                    "mode": "audio",
                    "channels": 1,
                    "tracks": [
                        {
                            "name": "配乐",
                            "clips": [
                                {
                                    "source": refs[0],
                                    "duration_ms": 2000,
                                    "gain_db": -12.0412 if role == "music_revision" else -6.0206,
                                }
                            ],
                        }
                    ],
                }
            else:
                dependencies = packet["dependencies"]
                combined = {
                    "receipts": [
                        receipt for dep in dependencies for receipt in receipts(dep["result"])
                    ]
                }
                picture = refs[0] if refs else output_from(combined, "image")
                music = refs[1] if len(refs) > 1 else output_from(combined, "audio")
                recipe = {
                    "mode": "sequence",
                    "clips": [
                        {"source": picture, "duration_ms": 1000},
                        {"source": picture, "duration_ms": 1000},
                    ],
                    "audio": [
                        {
                            "source": music,
                            "name": "固定配乐预览",
                            "duration_ms": 2000,
                            "fade_in_ms": 200,
                            "fade_out_ms": 200,
                        }
                    ],
                }
            return {
                "kind": "tool",
                "tool": "engineering.compose",
                "arguments": {
                    "work_id": packet["work_id"],
                    "base_revision": baseline["revision"],
                    "title": role,
                    "recipe": recipe,
                },
            }
        if not any(s.get("tool") == "engineering.preview" for s in steps):
            return {
                "kind": "tool",
                "tool": "engineering.preview",
                "arguments": composed["preview_arguments"],
            }
        assert any(
            s.get("data", {}).get("technical_validated")
            for s in steps
            if isinstance(s.get("data"), dict)
        ), "A submitted job is not a verified output"
        if role.startswith("assemble") and not packet["candidate_decision"]:
            return {
                "kind": "wait",
                "change_id": composed["change_id"],
                "fingerprint": composed["fingerprint"],
                "message": "请预览并采用这个剪辑候选。",
            }
        verified_outputs = next(
            s["data"]["outputs"]
            for s in reversed(steps)
            if isinstance(s.get("data"), dict) and s["data"].get("technical_validated")
        )
        return {
            "kind": "finish",
            "message": "真实预览通过技术校验；尚不代表创意质量评审通过。",
            "artifacts": [output["reference"] for output in verified_outputs],
        }


def run_until_idle(sessions, run_id):
    for _ in range(45):
        tickets = []
        for _ in range(2):
            with sessions() as db:
                ticket = service.claim(db, run_id)
                db.commit()
                if ticket:
                    tickets.append(ticket)
        if not tickets:
            with sessions() as db:
                run = db.get(ProductionRun, run_id)
                failed = [t for t in service.tasks(db, run) if t.status in {"failed", "uncertain"}]
                assert not failed, [(t.key, t.error, t.result) for t in failed]
                return run.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda t: runtime.execute(t, sessions), tickets))
    raise AssertionError("Production did not reach a persistent review/finish boundary")


def send(client, path, content, **extra):
    return post_ok(
        client,
        path + "/messages",
        {"request_key": str(uuid.uuid4()), "mode": "test", "content": content, **extra},
    )


def latest_video(db, task):
    checks = [
        s["receipt"]["data"]
        for s in task.state["steps"]
        if s.get("receipt", {}).get("tool") == "media.job_observe"
        and s["receipt"].get("data", {}).get("technical_validated")
    ]
    output = next(o for o in checks[-1]["outputs"] if o["kind"] == "video")
    return db.get(Blob, output["blob_hash"]), output


def test_single_chat_parallel_workers_replan_pause_render_and_human_adoption(
    studio, tmp_path, monkeypatch
):
    client, sessions, ids = studio
    monkeypatch.setattr(settings, "production_harness_enabled", True)
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    monkeypatch.setattr(capabilities, "_POLICIES", dict(capabilities._POLICIES))
    editing_tools.register_tools()
    media_tools.register_tools()
    picture = source(sessions, ids, png("red", (160, 90)))
    music = source(sessions, ids, sound(2), "audio", duration_ms=2000)
    adapter = ScriptedProduction(picture, music)
    monkeypatch.setitem(kernel._KERNELS, "mock", adapter)
    wid = work(studio)
    base = f"/api/v1/projects/{ids['project']}/conversations"
    conversation = post_ok(client, base, {"work_id": wid, "title": "真实工程制作验收"})
    path = f"{base}/{conversation['id']}"
    first = send(
        client, path, "离线机制验收：用指定红色图片和音频制作两秒短片，需可编辑并等我确认采用。"
    )
    run_id = uuid.UUID(first["messages"][-1]["task_id"])
    assert run_until_idle(sessions, run_id) == "needs_plan_approval"
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        revision = run.revision
    send(
        client,
        path,
        "确认方案，开始制作。",
        production={"run_id": str(run_id), "expected_revision": revision, "action": "approve_plan"},
    )
    assert run_until_idle(sessions, run_id) == "needs_attention"
    assert len(adapter.parallel_threads) == 2
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        rows = service.tasks(db, run)
        original_picture = next(t for t in rows if t.role == "picture")
        original_music = next(t for t in rows if t.role == "music")
        assembly = next(t for t in rows if t.role == "assemble")
        assert original_picture.status == original_music.status == "succeeded"
        assert assembly.status == "waiting_candidate"
        kept_id, kept_result = original_picture.id, copy.deepcopy(original_picture.result)
        old_music_id, old_assembly_id = original_music.id, assembly.id
        for task, kind in (
            (original_picture, "image.document"),
            (original_music, "audio.document"),
        ):
            candidate = candidate_from(task)
            document = db.get(
                EngineeringDocumentRevision,
                (uuid.UUID(candidate["document_ref"]["id"]), candidate["document_ref"]["revision"]),
            )
            assert candidate["document_kind"] == kind and document.payload
            assert db.get(EngineeringChange, uuid.UUID(candidate["change_id"])).status == "proposed"
        first_blob, _ = latest_video(db, assembly)
        original_video = tmp_path / "original-preview.mp4"
        original_video.write_bytes(cas.read_bytes(first_blob))
        assert db.get(CreativeWork, uuid.UUID(wid)).engineering_revision == 0
        assert db.scalar(select(func.count()).select_from(TimelineItem)) == 0
        revision = run.revision
    # The user specifies the local target through the conversation's supported
    # control contract. This verifies impact propagation without pretending that
    # a scripted decision adapter evaluates ambiguous creative feedback.
    guided = send(
        client,
        path,
        "只把配乐音量再降低六分贝，画面保持，重新出一个剪辑候选。",
        production={
            "run_id": str(run_id),
            "expected_revision": revision,
            "action": "steer",
            "task_ids": [str(old_music_id)],
        },
    )
    assert guided["messages"][-1]["task_id"] == str(run_id)
    latest = guided["messages"][-1]
    control = f"{path}/messages/{latest['id']}/control"
    post_ok(client, control, {"action": "pause"})
    with sessions() as db:
        assert service.claim(db, run_id) is None
        assert db.get(ProductionTask, kept_id).result == kept_result
        assert db.get(ProductionTask, old_music_id).status == "superseded"
        assert db.get(ProductionTask, old_assembly_id).status == "superseded"
        db.commit()
    post_ok(client, control, {"action": "resume"})
    assert run_until_idle(sessions, run_id) == "needs_plan_approval"
    assert adapter.preserved_seen["id"] == str(kept_id)
    with sessions() as db:
        revision = db.get(ProductionRun, run_id).revision
    send(
        client,
        path,
        "确认，只重新做配乐和剪辑。",
        production={"run_id": str(run_id), "expected_revision": revision, "action": "approve_plan"},
    )
    assert run_until_idle(sessions, run_id) == "needs_attention"
    with sessions() as db:
        final_task = next(
            t
            for t in service.tasks(db, db.get(ProductionRun, run_id))
            if t.role == "assemble_revision"
        )
        assert final_task.status == "waiting_candidate"
        candidate = candidate_from(final_task)
        blob, output = latest_video(db, final_task)
        final_path = tmp_path / "final-preview.mp4"
        final_path.write_bytes(cas.read_bytes(blob))
        assert output["technical_validation"]["status"] == "passed"
        assert db.get(ProductionTask, kept_id).result == kept_result
        assert db.get(CreativeWork, uuid.UUID(wid)).engineering_revision == 0
    # Decode rendered picture and soundtrack, not only database status flags.
    metadata = video_tools.probe_file(final_path)
    assert metadata["has_video"] and metadata["has_audio"]
    assert abs(metadata["duration_ms"] - 2000) < 100
    frame_path = tmp_path / "final-frame.png"
    media_engine.run(["-ss", "0.7", "-i", str(final_path), "-frames:v", "1", str(frame_path)])
    with Image.open(frame_path) as image:
        red, green, blue = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
        assert red > 220 and green < 30 and blue < 30

    def level(path, name):
        target = tmp_path / name
        media_engine.run(["-i", str(path), "-vn", "-ar", "48000", "-ac", "1", str(target)])
        with wave.open(str(target)) as audio:
            samples = array.array("h", audio.readframes(audio.getnframes()))[24000:72000]
        return math.sqrt(sum(s * s for s in samples) / len(samples))

    before, after = level(original_video, "original.wav"), level(final_path, "final.wav")
    assert 0.45 < after / before < 0.55
    review_path = (
        f"/api/v1/projects/{ids['project']}/works/{wid}/engineering"
        f"/changes/{candidate['change_id']}/accept"
    )
    post_ok(client, review_path, {"fingerprint": candidate["fingerprint"]})
    assert run_until_idle(sessions, run_id) == "succeeded"
    with sessions() as db:
        assert db.get(CreativeWork, uuid.UUID(wid)).engineering_revision == 1
        assert db.get(EngineeringChange, uuid.UUID(candidate["change_id"])).status == "accepted"
        assert db.scalar(select(func.count()).select_from(ProductionRun)) == 1
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 0
        assert db.scalar(select(func.count()).select_from(TimelineItem)) == 2
        assert db.scalar(select(func.count()).select_from(AudioTrack)) == 1
        assert all(float(job.actual_cost or 0) == 0 for job in db.scalars(select(GenerationJob)))
        assert not any(db.scalars(select(Generation.is_selected)))
    conversation = client.get(path).json()
    assert conversation["messages"][-1]["status"] == "completed"
    assert any(o["type"] == "video" for o in conversation["messages"][-1]["context"]["outputs"])
    assert len(conversation["messages"]) == 8
    evidence = {
        "decision_adapter": "scripted offline test; no creative-quality evaluation",
        "video": str(final_path),
        "frame": str(frame_path),
        "audio": str(tmp_path / "final.wav"),
        "duration_ms": metadata["duration_ms"],
        "revised_audio_rms_ratio": after / before,
        "parallel_worker_threads": len(adapter.parallel_threads),
        "user_adopted": True,
        "editable_boundary": "native drafts retained; sequence pins rendered previews",
    }
    (tmp_path / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    if destination := os.environ.get("HARNESS_PRODUCTION_EVIDENCE"):
        saved = Path(destination)
        saved.mkdir(parents=True, exist_ok=True)
        for name in (
            "final-preview.mp4",
            "final-frame.png",
            "final.wav",
            "original-preview.mp4",
            "original.wav",
        ):
            (saved / name).write_bytes((tmp_path / name).read_bytes())
        saved_evidence = {
            **evidence,
            "video": str(saved / "final-preview.mp4"),
            "frame": str(saved / "final-frame.png"),
            "audio": str(saved / "final.wav"),
        }
        (saved / "evidence.json").write_text(json.dumps(saved_evidence, indent=2), encoding="utf-8")
    print(f"Production evidence: {tmp_path / 'evidence.json'}")
