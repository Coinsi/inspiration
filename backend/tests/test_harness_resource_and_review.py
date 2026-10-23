"""Durable resource ceilings and candidate adoption wake-up against real DB."""

# ruff: noqa: F811
import uuid

import pytest

from app.core.errors import QuotaExceeded
from app.models.harness import ProductionRun, ProductionTask
from app.modules.harness import candidate_wait, resources, schemas, service
from tests.test_engineering import accept, request, setup
from tests.test_harness_context import context
from tests.test_harness_runtime import command, seeded
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_candidate_decision_wakes_only_current_waiter_preserving_explicit_pause(studio):
    client, sessions, ids, root, wid, source = setup(studio)
    path = f"{root}/works/{wid}/engineering"
    change = post_ok(client, path + "/changes", request(source))
    _, _, run_id = seeded(studio)
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        run.work_id = uuid.UUID(wid)
        task = service.tasks(db, run)[0]
        task.state = candidate_wait.begin(
            db,
            context(db, ids),
            run,
            task,
            schemas.WaitAction(
                kind="wait",
                change_id=change["id"],
                fingerprint=change["fingerprint"],
            ),
        )
        task.status = "waiting_candidate"
        service.reconcile(db, run)
        assert run.status == "needs_attention"
        command(db, ids, run_id, "pause")
        db.commit()
        task_id = task.id
    accept(client, path, change)
    with sessions() as db:
        assert service.claim(db, run_id) is None
        db.commit()
        task = db.get(ProductionTask, task_id)
        assert task.status == "queued"
        assert task.state["candidate_decision"]["adopted"] is True
        assert db.get(ProductionRun, run_id).status == "paused"
        command(db, ids, run_id, "resume")
        db.commit()
        assert service.claim(db, run_id)["task_id"] == str(task_id)


def test_run_action_ceiling_is_shared_not_reset_by_another_worker(studio):
    sessions, _, run_id = seeded(studio, max_total_actions=1)
    with sessions() as db:
        first = service.claim(db, run_id)
        service.finish_attempt(db, first, status="queued")
        db.commit()
        assert service.claim(db, run_id) is None
        run = db.get(ProductionRun, run_id)
        assert run.status == "paused" and run.error["code"] == "TOTAL_ACTION_LIMIT"


def test_preview_reservation_idempotent_and_not_refunded_by_unknown_result(studio):
    sessions, _, run_id = seeded(studio, max_preview_jobs=1)
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        ticket = {"attempt_id": str(uuid.uuid4())}
        resources.reserve_preview(db, run, ticket, {"change_id": "fixed"})
        resources.reserve_preview(db, run, ticket, {"change_id": "fixed"})
        db.commit()
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        with pytest.raises(QuotaExceeded):
            resources.reserve_preview(db, run, {"attempt_id": str(uuid.uuid4())}, {})


def test_real_annotated_search_returns_fixed_reference_and_coverage(studio):
    import io

    from PIL import Image

    from app.models.media_index import MediaAnnotation
    from app.modules.harness.search_tools import Search, search
    from app.modules.tools.contracts import ToolContext
    from tests.test_harness_context import seed

    _, sessions, ids = studio
    with sessions() as db:
        image = io.BytesIO()
        Image.new("RGB", (16, 16), "red").save(image, "PNG")
        reference = seed(db, ids, image.getvalue(), "image", "image/png")
        db.add(
            MediaAnnotation(
                version_id=uuid.UUID(reference["id"]),
                start_ms=0,
                end_ms=1000,
                kind="visual",
                text="红色武器",
                created_by=ids["user"],
            )
        )
        db.flush()
        result = search(db, context(db, ids), ToolContext(), Search(mode="annotated", query="武器"))
        assert result["content_verified"] is False
        assert result["coverage"] and len(result["items"]) == 1
        assert (
            result["items"][0]["reference"]["expected_blob_hash"]
            == __import__("hashlib").sha256(image.getvalue()).hexdigest()
        )
