"""Task browsing, exact filtered counts, source isolation and existing commands."""

# ruff: noqa: F811
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.models.generation import GenerationJob
from app.models.identity import Membership
from app.models.shot import Shot
from app.modules.generation import jobs, media_engine
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_catalog_pagination_filters_counts_and_source_isolation(studio):
    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    with sessions() as db:
        shot = db.get(Shot, uuid.UUID(ids["shot"]))
        shot.title = "海边 100%_镜头"
        stamp = datetime.now(UTC)
        for i in range(32):
            db.add(
                GenerationJob(
                    project_id=uuid.UUID(ids["project"]),
                    target_id=shot.id,
                    target_type="shot",
                    provider="local",
                    request_type="image",
                    status="failed" if i < 3 else "pending" if i == 3 else "succeeded",
                    input_snapshot={"operation": "refine", "large_input": "x" * 5000},
                    created_at=stamp,
                )
            )
        # Malformed legacy source pointer must not reveal the other project's title.
        foreign = Shot(
            project_id=ids["other"],
            scene_id=shot.scene_id,
            code="OTHER-SHOT",
            ordinal=2,
            title="PRIVATE",
        )
        db.add(foreign)
        db.flush()
        own = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_type="shot",
            target_id=foreign.id,
            provider="mock",
            status="failed",
            input_snapshot={},
        )
        db.add(own)
        db.commit()
        own_id = str(own.id)
    first = client.get(base + "/jobs/catalog?limit=24").json()
    assert first["total"] == 33 and len(first["items"]) == 24 and first["has_more"]
    second = client.get(base + "/jobs/catalog?limit=24&offset=24").json()
    assert len(second["items"]) == 9 and not second["has_more"]
    assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
    assert "input_snapshot" not in str(first) and "large_input" not in str(first)
    scoped = client.get(
        base + "/jobs/catalog",
        params={"search": "100%_", "operation": "refine", "status": "failed"},
    ).json()
    assert scoped["total"] == 3 and scoped["counts"] == {"failed": 3, "pending": 1, "succeeded": 28}
    active = client.get(base + "/jobs/catalog?status=active").json()
    assert active["total"] == 1
    assert client.get(base + "/jobs/catalog?status=unknown").status_code == 422
    assert client.get(base + "/jobs/catalog?limit=101").status_code == 422
    assert client.get(base + "/jobs/catalog?offset=-1").status_code == 422
    assert client.get(base + "/jobs/catalog?search=PRIVATE").json()["total"] == 0
    broken = client.get(base + f"/jobs/{own_id}/workspace").json()
    assert broken["summary"]["target_name"] is None
    assert not broken["summary"]["target_available"] and not broken["can_retry"]
    assert client.get(f"/api/v1/projects/{ids['other']}/jobs/catalog").status_code == 403
    assert client.get(f"/api/v1/projects/{ids['other']}/jobs/{own_id}/workspace").status_code == 403


def test_workspace_outputs_retry_permissions_and_read_only(studio, monkeypatch):
    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}"
    uploaded = client.post(
        f"{base}/media/shot/{ids['shot']}/upload",
        files={"file": ("a.png", media_engine.mock_image(), "image/png")},
    ).json()
    path = base + f"/jobs/{uploaded['job_id']}/workspace"
    data = client.get(path).json()
    assert data["summary"]["result_count"] == 1
    assert data["outputs"][0]["output_mime"] == "image/png"
    assert data["outputs"][0]["id"] == uploaded["id"] and not data["can_retry"]
    assert client.get(path).json()["job"]["updated_at"] == data["job"]["updated_at"]
    with sessions() as db:
        job = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_id=uuid.UUID(ids["shot"]),
            target_type="shot",
            provider="mock",
            status="failed",
            input_snapshot={"operation": "generate"},
        )
        db.add(job)
        db.commit()
        old_id = str(job.id)
    old = base + f"/jobs/{old_id}"
    assert client.get(old + "/workspace").json()["can_retry"]
    monkeypatch.setattr(jobs, "dispatch", lambda db, job: job)
    retried = post_ok(client, old + "/retry")
    detail = client.get(old + "/workspace").json()
    assert detail["retries"][0]["id"] == retried["id"] and not detail["can_retry"]
    child = base + f"/jobs/{retried['id']}"
    assert client.get(child + "/workspace").json()["retry_parent"]["id"] == old_id
    assert client.get(child + "/workspace").json()["can_cancel"]
    post_ok(client, child + "/cancel")
    assert not client.get(child + "/workspace").json()["can_cancel"]
    assert client.get(child + "/workspace").json()["can_retry"]
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "viewer"
        db.commit()
    assert not client.get(child + "/workspace").json()["can_retry"]
    assert client.post(child + "/retry").status_code == 403
    with sessions() as db:
        db.get(Shot, uuid.UUID(ids["shot"])).deleted_at = datetime.now(UTC)
        db.commit()
    removed = client.get(path).json()
    assert not removed["summary"]["target_available"] and len(removed["outputs"]) == 1


def test_more_retries_cannot_hide_existing_success(studio):
    client, sessions, ids = studio
    with sessions() as db:
        parent = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_id=uuid.UUID(ids["shot"]),
            provider="mock",
            status="failed",
        )
        db.add(parent)
        db.flush()
        for i in range(23):
            db.add(
                GenerationJob(
                    project_id=parent.project_id,
                    target_id=parent.target_id,
                    provider="mock",
                    status="succeeded" if i == 0 else "failed",
                    input_snapshot={"retry_of": str(parent.id)},
                )
            )
            db.flush()
        db.commit()
        pid = str(parent.id)
    data = client.get(f"/api/v1/projects/{ids['project']}/jobs/{pid}/workspace").json()
    assert len(data["retries"]) == 20 and data["more_retries"] and not data["can_retry"]
