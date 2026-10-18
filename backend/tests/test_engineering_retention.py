"""Real DB retention roots, including opaque JSON and other project references."""

# ruff: noqa: F811
import uuid

from app.models.generation import GenerationJob
from app.models.identity import Project
from app.models.storage import Blob
from app.storage import retention
from tests.test_engineering import accept, request, setup
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_candidate_history_and_other_tenant_retain_content(studio):
    client, sessions, ids, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    data = request(source)
    candidate = post_ok(client, path + "/changes", data)
    with sessions() as db:
        report = retention.inspect_blob(db, "a" * 64)
        assert report["retained"] and not report["physical_delete_allowed"]
        assert {r["table"] for r in report["reasons"]} >= {
            "engineering_resource",
            "engineering_change",
            "engineering_document_revision",
        }
    accept(client, path, candidate)
    remove = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 1,
            "commands": [
                {"operation": "resource.remove", "usage_id": data["commands"][0]["usage_id"]}
            ],
        },
    )
    accept(client, path, remove)
    with sessions() as db:
        # A distinct tenant retains the same physical bytes independently.
        db.get(Project, ids["other"]).cover_blob_hash = "a" * 64
        db.commit()
        report = retention.inspect_blob(db, "a" * 64)
        assert {r["table"] for r in report["reasons"]} >= {"engineering_commit", "project"}
        assert all(set(r) == {"table", "field", "kind"} for r in report["reasons"])


def test_task_and_unknown_extension_roots_are_not_ignored(studio):
    _, sessions, ids = studio
    digest = "b" * 64
    with sessions() as db:
        db.add(Blob(hash=digest, storage_uri="test", mime="image/png", size_bytes=1))
        db.flush()
        assert not retention.inspect_blob(db, digest)["retained"]
        job = GenerationJob(
            project_id=ids["project"],
            target_type="shot",
            target_id=ids["shot"],
            provider="local",
            status="running",
            input_snapshot={"extension.future": [{"source": digest}]},
        )
        db.add(job)
        db.flush()
        assert any(
            r["table"] == "generation_job" for r in retention.inspect_blob(db, digest)["reasons"]
        )
        job.status = "succeeded"
        db.flush()
        # Completed task provenance is retained too; no silent expiry policy.
        assert retention.inspect_blob(db, digest)["retained"]
        job.input_snapshot = {"source": "prefix" + digest}
        db.flush()
        assert not retention.inspect_blob(db, digest)["retained"]
        assert not retention.inspect_blob(db, digest)["physical_delete_allowed"]
        job.input_snapshot = {"extension.future": {digest: {"kind": "cached_mask"}}}
        db.flush()
        assert retention.inspect_blob(db, digest)["retained"]
        db.rollback()
