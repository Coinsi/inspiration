"""Compound edits and concurrent review acceptance."""

# ruff: noqa: F811
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from app.core.errors import Conflict
from app.models.enums import Role
from app.models.generation import Generation, GenerationJob
from app.modules.engineering import schemas, service
from app.storage import cas
from tests.test_engineering import accept
from tests.test_engineering_review import draft, preview, ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401
from tests.test_timeline_tracks import tone


def test_multitrack_candidate_preserves_audio_caption_and_overlay(studio):
    client, sessions, ids, path, tid, url = ready(studio)
    with sessions() as db:
        blob = cas.put_bytes(db, tone(1), "audio/wav", duration_ms=1000)
        job = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_type="timeline",
            target_id=uuid.UUID(tid),
            provider="local",
            status="succeeded",
        )
        db.add(job)
        db.flush()
        generation = Generation(
            project_id=job.project_id,
            job_id=job.id,
            target_type="timeline",
            target_id=job.target_id,
            provider="local",
            output_type="audio",
            output_blob_hash=blob.hash,
        )
        db.add(generation)
        db.flush()
        gid = str(generation.id)
        db.commit()
    doc = client.get(url).json()
    doc["audio"] = [{"generation_id": gid, "duration_ms": 800, "gain_db": -12}]
    doc["subtitles"] = [{"text": "清晨", "start_ms": 100, "end_ms": 800}]
    doc["visuals"] = [
        {
            "id": str(uuid.uuid4()),
            "generation_id": doc["items"][0]["generation_id"],
            "duration_ms": 800,
            "opacity": 0.5,
        }
    ]
    candidate = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [{"operation": "timeline.edit", "timeline_id": tid, "document": doc}],
        },
    )
    assert len(candidate["snapshot"]["resources"]) == 3
    assert preview(client, path, candidate)["preview"]["status"] == "succeeded"
    accept(client, path, candidate)
    saved = client.get(url).json()
    assert saved["audio"][0]["gain_db"] == -12
    assert saved["visuals"][0]["opacity"] == 0.5
    assert saved["subtitles"][0]["text"] == "清晨"
    saved["audio"][0]["gain_db"] = -6
    updated = client.put(url, json=saved).json()
    assert updated["audio"][0]["id"] == saved["audio"][0]["id"]
    assert updated["subtitles"][0]["id"] == saved["subtitles"][0]["id"]


def test_concurrent_review_acceptance_has_one_atomic_winner(studio):
    client, sessions, ids, path, tid, url = ready(studio)
    candidates = [draft(client, path, tid, url, duration=d) for d in (500, 800)]
    for c in candidates:
        assert preview(client, path, c)["preview"]["status"] == "succeeded"
    ctx = SimpleNamespace(
        project=SimpleNamespace(id=uuid.UUID(ids["project"])),
        user=SimpleNamespace(id=ids["user"]),
        role=Role.admin,
    )
    wid = uuid.UUID(path.split("/works/")[1].split("/")[0])

    def submit(c):
        with sessions() as db:
            try:
                service.accept(
                    db, ctx, wid, uuid.UUID(c["id"]), schemas.Accept(fingerprint=c["fingerprint"])
                )
                db.commit()
                return "accepted"
            except Conflict:
                db.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, candidates))
    assert sorted(results) == ["accepted", "conflict"]
    assert client.get(url).json()["revision"] == 1
    assert client.get(path).json()["revision"] == 1
