"""Unified task projection, privacy, recovery and truthful change feedback."""

# ruff: noqa: F811
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.models.agent import AgentRun
from app.models.canvas import Canvas, CanvasRun
from app.models.conversation import Conversation, ConversationMessage
from app.models.generation import GenerationJob
from app.models.identity import AuditLog, Membership, User
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaIndex
from app.models.media_processing import MediaProcessingRun
from app.models.storage import Blob
from app.models.transcription import TranscriptionRun
from app.modules.transcription import service as transcription
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def seed(sessions, ids):
    with sessions() as db:
        stamp = datetime.now(UTC)
        db.add(Blob(hash="a" * 64, storage_uri="test.mp4", mime="video/mp4", size_bytes=10))
        db.flush()
        media = LibraryMedia(project_id=ids["project"], name="海边 100%_素材")
        db.add(media)
        db.flush()
        version = MediaVersion(
            media_id=media.id,
            ordinal=1,
            filename="sea.mp4",
            fingerprint="a" * 64,
            size_bytes=10,
            status="ready",
            created_at=stamp,
        )
        db.add(version)
        db.flush()
        canvas = Canvas(
            project_id=ids["project"], name="海边画布", created_by=ids["user"], document={}
        )
        db.add(canvas)
        db.flush()
        rows = {
            "generation": GenerationJob(
                project_id=ids["project"],
                target_type="shot",
                target_id=ids["shot"],
                provider="mock",
                status="failed",
                cost_raw={"warnings": None},
            ),
            "agent": AgentRun(
                project_id=ids["project"],
                created_by=ids["user"],
                request_key=uuid.uuid4(),
                goal="海边对话",
                engine="test",
                status="paused",
                scope=[],
                steps=[],
            ),
            "canvas": CanvasRun(
                project_id=ids["project"],
                canvas_id=canvas.id,
                revision=1,
                request_key=uuid.uuid4(),
                status="failed",
                snapshot={},
                steps={},
                created_by=ids["user"],
            ),
            "index": MediaIndex(
                version_id=version.id, model_key="test", status="processing", total=10, completed=3
            ),
            "processing": MediaProcessingRun(
                version_id=version.id,
                created_by=ids["user"],
                provider="wise",
                operation="process",
                source_hash="a" * 64,
                status="waiting",
            ),
            "transcription": TranscriptionRun(
                project_id=ids["project"],
                version_id=version.id,
                model_key="test",
                status="failed",
                created_by=ids["user"],
                completed=3,
                total=10,
            ),
        }
        for row in rows.values():
            row.created_at = stamp
            db.add(row)
        db.flush()
        rows["import"] = version
        result = {key: str(row.id) for key, row in rows.items()}
        result["media"] = str(media.id)
        db.commit()
        return result


def test_all_task_types_paginated_counts_and_attention(studio):
    client, sessions, ids = studio
    seeded = seed(sessions, ids)
    base = f"/api/v1/projects/{ids['project']}/activity"
    first = client.get(base + "?limit=3").json()
    assert first["total"] == 7 and first["has_more"]
    pages = [client.get(base, params={"limit": 3, "offset": offset}).json() for offset in (0, 3, 6)]
    keys = [(r["kind"], r["id"]) for page in pages for r in page["items"]]
    assert len(set(keys)) == len(keys) == 7
    assert {k for k, _ in keys} == set(seeded) - {"media"}
    assert client.get(base + "?status=attention").json()["total"] == 4
    assert client.get(base + "?status=active").json()["total"] == 2
    assert client.get(base, params={"search": "100%_"}).json()["total"] == 4
    assert client.get(base + "?operation=transcription").json()["total"] == 1
    assert "snapshot" not in str(first)
    assert client.get(base + "?status=unknown").status_code == 422
    assert client.get(base + "?limit=101").status_code == 422
    assert client.get(f"/api/v1/projects/{ids['other']}/activity").status_code == 403


def test_private_conversation_excluded_from_counts_search_and_details(studio):
    client, sessions, ids = studio
    seeded = seed(sessions, ids)
    with sessions() as db:
        other = User(username="private", email="private@example.com", display_name="Private")
        db.add(other)
        db.flush()
        c = Conversation(project_id=ids["project"], created_by=other.id, title="秘密对话")
        db.add(c)
        db.flush()
        run = db.get(AgentRun, uuid.UUID(seeded["agent"]))
        run.created_by = other.id
        db.add(
            ConversationMessage(
                conversation_id=c.id,
                ordinal=1,
                role="assistant",
                request_key=uuid.uuid4(),
                execution_kind="project_agent",
                execution_id=run.id,
            )
        )
        db.commit()
    base = f"/api/v1/projects/{ids['project']}/activity"
    assert client.get(base).json()["total"] == 6
    assert client.get(base + "?operation=agent").json()["total"] == 0
    assert client.get(base + "?search=海边对话").json()["total"] == 0
    assert client.get(base + f"/agent/{seeded['agent']}").status_code == 404


def test_detail_recovery_permissions_history_and_version_pin(studio, monkeypatch):
    client, sessions, ids = studio
    seeded = seed(sessions, ids)
    root = f"/api/v1/projects/{ids['project']}"
    path = root + f"/activity/transcription/{seeded['transcription']}"
    data = client.get(path).json()
    assert data["progress"] == {"completed": 3, "total": 10}
    assert data["source"]["path"].endswith(
        f"version={seeded['import']}&run={seeded['transcription']}"
    )
    action = data["actions"][0]
    monkeypatch.setattr(transcription, "inference", lambda path: {"model_key": "test"})
    post_ok(client, "/api/v1" + action["path"], action["payload"])
    after = client.get(path).json()
    assert after["summary"]["status"] == "pending" and after["progress"]["completed"] == 3
    assert after["history"][0]["label"] == "已续接转写"
    source = client.get(root + f"/activity/import/{seeded['import']}").json()
    assert f"version={seeded['import']}" in source["source"]["path"]
    assert "不等于" in source["result_note"]
    with sessions() as db:
        member = db.scalar(select(Membership).where(Membership.project_id == ids["project"]))
        member.role = "viewer"
        db.commit()
    for kind in ("agent", "canvas", "index", "processing", "transcription"):
        assert client.get(root + f"/activity/{kind}/{seeded[kind]}").json()["actions"] == []
    assert client.post("/api/v1" + action["path"], json=action["payload"]).status_code == 403


def test_proposed_applied_changes_and_archived_conversation_recovery(studio):
    client, sessions, ids = studio
    seeded = seed(sessions, ids)
    with sessions() as db:
        run = db.get(AgentRun, uuid.UUID(seeded["agent"]))
        run.steps = [
            {
                "id": "one",
                "tool": "object.propose_edit",
                "arguments": {
                    "target_type": "shot",
                    "target_id": ids["shot"],
                    "summary": "修改镜头",
                },
                "result": {"before": {"title": "旧标题"}, "after": {"title": "新标题"}},
            }
        ]
        c = Conversation(
            project_id=ids["project"],
            created_by=ids["user"],
            title="存档",
            archived_at=datetime.now(UTC),
        )
        db.add(c)
        db.flush()
        db.add(
            ConversationMessage(
                conversation_id=c.id,
                ordinal=1,
                role="assistant",
                request_key=uuid.uuid4(),
                execution_kind="project_agent",
                execution_id=run.id,
            )
        )
        db.add(
            AuditLog(
                project_id=ids["project"],
                target_id=run.id,
                action="provider.update",
                detail={"secret": "MUST NOT LEAK"},
            )
        )
        db.commit()
    path = f"/api/v1/projects/{ids['project']}/activity/agent/{seeded['agent']}"
    data = client.get(path).json()
    assert data["changes"][0]["state"] == "proposed"
    assert data["changes"][0]["before"]["title"] == "旧标题"
    assert not any(a["name"] == "resume" for a in data["actions"])
    assert "归档" in data["recovery_note"]
    assert "MUST NOT LEAK" not in str(data)
    with sessions() as db:
        run = db.get(AgentRun, uuid.UUID(seeded["agent"]))
        run.steps = [{**run.steps[0], "decision": "approve", "applied": {"version_id": "v1"}}]
        db.commit()
    assert client.get(path).json()["changes"][0]["state"] == "applied"
