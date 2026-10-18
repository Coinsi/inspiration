"""Timeline capture integration; immutable shadow state without legacy writes."""

# ruff: noqa: F811
import uuid

from app.models.generation import Generation, GenerationJob
from app.models.timeline import Timeline, TimelineItem
from tests.test_engineering import accept, setup
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def prepared(studio):
    client, sessions, ids, root, wid, _ = setup(studio)
    with sessions() as db:
        job = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_id=uuid.UUID(ids["shot"]),
            provider="mock",
            status="succeeded",
        )
        db.add(job)
        db.flush()
        generation = Generation(
            project_id=job.project_id,
            job_id=job.id,
            target_id=job.target_id,
            target_type="shot",
            provider="mock",
            output_blob_hash="a" * 64,
            output_type="image",
        )
        db.add(generation)
        timeline = Timeline(
            project_id=job.project_id, name="测试剪辑", kind="rough", created_by=ids["user"]
        )
        db.add(timeline)
        db.flush()
        db.add(
            TimelineItem(
                timeline_id=timeline.id,
                shot_id=job.target_id,
                generation_id=generation.id,
                ordinal=0,
                duration_ms=1000,
            )
        )
        tid = str(timeline.id)
        db.commit()
    data = {
        "request_key": str(uuid.uuid4()),
        "base_revision": 0,
        "commands": [{"operation": "timeline.capture", "timeline_id": tid, "timeline_revision": 0}],
    }
    return client, sessions, ids, f"{root}/works/{wid}/engineering", tid, data


def test_capture_is_fixed_and_does_not_change_editor(studio):
    client, sessions, _, path, tid, data = prepared(studio)
    change = post_ok(client, path + "/changes", data)
    frozen = change["snapshot"]["timeline_captures"][tid]
    assert frozen["render_snapshot"]["clips"][0]["blob_hash"] == "a" * 64
    assert len(change["snapshot"]["resources"]) == 1
    assert client.get(path).json()["revision"] == 0
    accept(client, path, change)
    with sessions() as db:
        timeline = db.get(Timeline, uuid.UUID(tid))
        assert timeline.revision == 0
        timeline.revision = 1
        timeline.name = "其他页面修改"
        db.commit()
    assert client.get(path).json()["snapshot"]["timeline_captures"][tid] == frozen
    # Retries return the original frozen candidate, not a new capture of changing sources.
    assert post_ok(client, path + "/changes", data)["id"] == change["id"]


def test_capture_stale_revision_and_foreign_timeline_rejected(studio):
    client, sessions, ids, path, tid, data = prepared(studio)
    data["commands"][0]["timeline_revision"] = 1
    assert client.post(path + "/changes", json=data).status_code == 409
    data["commands"][0]["timeline_revision"] = 0
    with sessions() as db:
        db.get(Timeline, uuid.UUID(tid)).project_id = ids["other"]
        db.commit()
    assert client.post(path + "/changes", json=data).status_code == 404
    assert client.get(path + "/changes").json() == []


def test_capture_dependency_cannot_be_removed_as_loose_resource(studio):
    client, _, _, path, _, data = prepared(studio)
    change = post_ok(client, path + "/changes", data)
    accept(client, path, change)
    data.update(
        request_key=str(uuid.uuid4()),
        base_revision=1,
        commands=[
            {
                "operation": "resource.remove",
                "usage_id": change["snapshot"]["resources"][0]["usage_id"],
            }
        ],
    )
    assert client.post(path + "/changes", json=data).status_code == 409


def test_recapture_replaces_uses_without_duplication(studio):
    client, _, _, path, _, data = prepared(studio)
    change = post_ok(client, path + "/changes", data)
    accept(client, path, change)
    data.update(request_key=str(uuid.uuid4()), base_revision=1)
    next_change = post_ok(client, path + "/changes", data)
    assert next_change["snapshot"] == change["snapshot"]


def test_captured_snapshot_reuses_real_renderer(studio, tmp_path):
    from app.modules.generation import media_engine

    client, _, _, path, tid, data = prepared(studio)
    change = post_ok(client, path + "/changes", data)
    snapshot = change["snapshot"]["timeline_captures"][tid]["render_snapshot"]
    output = tmp_path / "captured.mp4"
    output.write_bytes(
        media_engine.render(
            snapshot["clips"],
            lambda _: media_engine.mock_image(),
            {**snapshot["tracks"], "height": 720, "aspect_ratio": "16:9"},
        )
    )
    decoded = media_engine.run(["-i", str(output), "-f", "null", "-"])
    assert "1280x720" in decoded and "00:00:01." in decoded
