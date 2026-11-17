"""Durable adaptation receipts survive navigation and duplicate submissions."""

# ruff: noqa: F811
from sqlalchemy import select, func
from app.models.narrative import Script
from app.modules.narrative import tasks, service
from tests.test_media_workflow import studio  # noqa: F401


def test_script_task_dedup_completion_and_retry(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    imported = client.post(
        root + "/novels/import",
        files={"file": ("story.txt", "第一章 清晨\n少年推开门。".encode(), "text/plain")},
    ).json()
    chapter = client.get(root + f"/novels/{imported['id']}/directory").json()["chapters"][0]["id"]
    payload = {"chapter_id": chapter, "strategy": "mock"}
    monkeypatch.setattr(tasks, "SessionLocal", sessions)
    assert client.get(root + f"/chapters/{chapter}/script-task").json() is None
    first = client.post(root + "/script-tasks", json=payload)
    assert first.status_code == 202
    task = first.json()
    assert task["status"] == "queued"
    assert client.post(root + "/script-tasks", json=payload).json()["id"] == task["id"]
    assert tasks.run_once()
    done = client.get(root + f"/chapters/{chapter}/script-task").json()
    assert done["status"] == "succeeded" and done["script_id"]
    assert client.post(root + "/script-tasks", json=payload).json()["id"] == task["id"]
    assert not tasks.run_once()
    with sessions() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Script).where(Script.source_chapter_id == chapter)
            )
            == 1
        )


def test_script_failure_requires_explicit_retry(studio, monkeypatch):
    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    imported = client.post(
        root + "/novels/import",
        files={"file": ("story.txt", "第一章 清晨\n少年推开门。".encode(), "text/plain")},
    ).json()
    chapter = client.get(root + f"/novels/{imported['id']}/directory").json()["chapters"][0]["id"]
    payload = {"chapter_id": chapter, "strategy": "mock"}
    monkeypatch.setattr(tasks, "SessionLocal", sessions)

    def fail(*args):
        raise RuntimeError("private provider error")

    monkeypatch.setattr(service, "generate_script_from_chapter", fail)
    first = client.post(root + "/script-tasks", json=payload).json()
    assert tasks.run_once()
    failed = client.get(root + f"/chapters/{chapter}/script-task").json()
    assert failed["status"] == "failed" and "private" not in failed["error"]
    assert client.post(root + "/script-tasks", json=payload).json()["id"] == first["id"]
    retry = client.post(root + f"/script-tasks?retry_task_id={first['id']}", json=payload).json()
    assert retry["id"] != first["id"] and retry["status"] == "queued"
    assert (
        client.post(root + f"/script-tasks?retry_task_id={first['id']}", json=payload).json()["id"]
        == retry["id"]
    )


def test_interrupted_task_is_not_automatically_regenerated(studio, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from app.models.narrative import ScriptGenerationTask

    client, sessions, ids = studio
    root = f"/api/v1/projects/{ids['project']}"
    imported = client.post(
        root + "/novels/import",
        files={"file": ("story.txt", "第一章 清晨\n少年推开门。".encode(), "text/plain")},
    ).json()
    chapter = client.get(root + f"/novels/{imported['id']}/directory").json()["chapters"][0]["id"]
    task = client.post(root + "/script-tasks", json={"chapter_id": chapter}).json()
    monkeypatch.setattr(tasks, "SessionLocal", sessions)
    with sessions() as db:
        row = db.get(ScriptGenerationTask, task["id"])
        row.status = "running"
        row.updated_at = datetime.now(UTC) - timedelta(minutes=11)
        db.commit()
    monkeypatch.setattr(
        service,
        "generate_script_from_chapter",
        lambda *args: (_ for _ in ()).throw(AssertionError("Must not resubmit")),
    )
    assert tasks.run_once()
    assert client.get(root + f"/chapters/{chapter}/script-task").json()["status"] == "failed"
    assert not tasks.run_once()
