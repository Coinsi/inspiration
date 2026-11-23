"""Original-step recovery with real PostgreSQL and strictly offline providers."""

# ruff: noqa: F811
import copy
import uuid

import pytest
from sqlalchemy import func, select

from app.adapters.generation.mock import MockProvider
from app.models.creation import Creation
from app.models.generation import GenerationJob
from app.models.harness import ProductionAttempt, ProductionRun, ProductionTask
from app.modules.billing import wallet
from app.modules.harness import kernel, runtime, service
from tests.test_generation_billing import account, configure
from tests.test_harness_job_wait import prepared
from tests.test_harness_media_tools import tools  # noqa: F401
from tests.test_harness_runtime import plan, registered_fixture_kernel, seeded  # noqa: F401
from tests.test_media_workflow import studio  # noqa: F401


def claim(sessions, rid):
    with sessions() as db:
        ticket = service.claim(db, rid)
        db.commit()
        return ticket


def endpoint(ids, rid):
    return f"/api/v1/projects/{ids['project']}/production/runs/{rid}"


def retry_request(client, ids, rid, tid, **updates):
    current = client.get(endpoint(ids, rid)).json()
    body = {
        "request_key": str(uuid.uuid4()),
        "expected_revision": current["revision"],
        "action": "retry",
        "task_ids": [str(tid)],
        **updates,
    }
    return client.post(endpoint(ids, rid) + "/control", json=body), body


@pytest.mark.parametrize("kind", ["tool", "agent"])
def test_credit_failure_waits_then_retries_original_step_once(studio, monkeypatch, kind):
    client, sessions, ids = studio
    model = configure(studio, credits=100)
    arguments = {
        "provider": model["provider_name"],
        "prompt": "雨夜的街道，保留原构图与镜头要求",
        "request_type": "image",
        "params": {},
        "provider_params": {},
        "references": [],
    }
    operation = {"kind": "tool", "tool": "media.generate", "arguments": arguments}
    spec = {"kind": kind, "expected_output": "artifact", "dependencies": ["completed"]}
    if kind == "tool":
        spec.update(tool="media.generate", arguments=arguments)
    else:
        spec["tools"] = ["media.generate"]
        monkeypatch.setitem(kernel._KERNELS, "fixture", lambda *_: copy.deepcopy(operation))
    _, _, rid = seeded(
        studio,
        plan(("completed", {}), ("generate", spec)),
        budget_points=100,
        effects=["read", "generate"],
    )
    completed = claim(sessions, rid)
    with sessions() as db:
        service.finish_attempt(db, completed, status="succeeded", result={"kept": "原完成结果"})
        db.commit()
    submitted = []
    original_submit = MockProvider.submit

    def observed_submit(self, request):
        submitted.append(request)
        return original_submit(self, request)

    monkeypatch.setattr(MockProvider, "submit", observed_submit)
    failed_ticket = claim(sessions, rid)
    runtime.execute(failed_ticket, sessions)
    assert submitted == []
    response = client.get(endpoint(ids, rid))
    assert response.status_code == 200, response.text
    failed = next(task for task in response.json()["tasks"] if task["key"] == "generate")
    assert failed["status"] == "failed", failed
    assert failed["error"]["retryable"] is True
    assert failed["error"]["detail"] == {
        "reason": "insufficient_personal_credits",
        "required_units": 125,
        "available_units": 100,
        "shortfall_units": 25,
    }
    assert claim(sessions, rid) is None  # no implicit model loop or paid replay
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0
        assert db.scalar(select(func.count()).select_from(Creation)) == 0
        original_arguments = db.get(
            ProductionAttempt, uuid.UUID(failed_ticket["attempt_id"])
        ).operation["arguments"]
        assert {key: original_arguments[key] for key in arguments} == arguments
        wallet.change(db, ids["user"], "test_funding", uuid.uuid4(), available=100)
        db.commit()
    assert claim(sessions, rid) is None  # replenishing alone does not authorize retry
    response, request = retry_request(client, ids, rid, failed["id"])
    assert response.status_code == 200, response.text
    repeated = client.post(endpoint(ids, rid) + "/control", json=request)
    assert repeated.status_code == 200, repeated.text
    conflicting, _ = retry_request(client, ids, rid, failed["id"])
    assert conflicting.status_code == 409
    with sessions() as db:
        task = db.get(ProductionTask, uuid.UUID(failed["id"]))
        assert task.state["pending_action"] == {**operation, "arguments": original_arguments}
        assert task.state["retries"] == 1
        done = db.get(ProductionTask, uuid.UUID(completed["task_id"]))
        assert done.status == "succeeded" and done.result == {"kept": "原完成结果"}
        assert done.attempts == 1
    # A retry must consume the stored action even for an agent task; no new model decision.
    monkeypatch.setitem(kernel._KERNELS, "fixture", lambda *_: pytest.fail("rewrote original task"))
    retry_ticket = claim(sessions, rid)
    runtime.execute(retry_ticket, sessions)
    runtime.execute(retry_ticket, sessions)  # stale worker delivery is fenced
    assert len(submitted) == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert db.scalar(select(func.count()).select_from(Creation)) == 1
        attempt = db.get(ProductionAttempt, uuid.UUID(retry_ticket["attempt_id"]))
        assert attempt.operation["arguments"] == original_arguments
    assert account(studio) == (75, 0)


def legacy_failure(studio):
    sessions, ids, rid = seeded(studio)
    ticket = claim(sessions, rid)
    saved = {
        "provider": "mock",
        "prompt": "已确认的提示词",
        "request_type": "image",
        "params": {"aspect_ratio": "16:9", "seed": 42},
        "references": [{"source_type": "attachment", "source_id": str(uuid.uuid4())}],
    }
    with sessions() as db:
        attempt = db.get(ProductionAttempt, uuid.UUID(ticket["attempt_id"]))
        attempt.operation = {"phase": "tool", "tool": "media.generate", "arguments": saved}
        service.finish_attempt(
            db,
            ticket,
            status="failed",
            error={"code": "QUOTA_EXCEEDED", "message": "历史积分不足", "retryable": False},
        )
        db.commit()
    return sessions, ids, rid, uuid.UUID(ticket["task_id"]), saved, ticket


def test_legacy_failure_preserves_prompt_attachment_parameters(studio):
    client = studio[0]
    sessions, ids, rid, tid, saved, _ = legacy_failure(studio)
    response, _ = retry_request(client, ids, rid, tid)
    assert response.status_code == 200, response.text
    with sessions() as db:
        assert db.get(ProductionTask, tid).state["pending_action"]["arguments"] == saved


def test_adjusting_resource_limits_does_not_replay_failed_generation(studio):
    client = studio[0]
    sessions, ids, rid, tid, _, _ = legacy_failure(studio)
    current = client.get(endpoint(ids, rid)).json()
    response = client.post(
        endpoint(ids, rid) + "/control",
        json={
            "request_key": str(uuid.uuid4()),
            "expected_revision": current["revision"],
            "action": "update_limits",
            "limits": {"max_actions": 50},
        },
    )
    assert response.status_code == 200, response.text
    with sessions() as db:
        assert db.get(ProductionTask, tid).status == "failed"
    assert claim(sessions, rid) is None


@pytest.mark.parametrize(
    "barrier", ["creation", "waiting", "receipt", "uncertain", "canceled", "limit"]
)
def test_blocked_or_ended_work_cannot_be_replayed(studio, barrier):
    client = studio[0]
    sessions, ids, rid, tid, saved, ticket = legacy_failure(studio)
    with sessions() as db:
        task, run = db.get(ProductionTask, tid), db.get(ProductionRun, rid)
        if barrier == "creation":
            db.add(
                Creation(
                    project_id=run.project_id,
                    created_by=run.created_by,
                    request_key=uuid.UUID(ticket["attempt_id"]),
                    title="已有任务",
                    request=saved,
                )
            )
        elif barrier == "waiting":
            task.state = {"waiting_job_id": str(uuid.uuid4())}
        elif barrier == "receipt":
            task.error = {**task.error, "recovery": "resume_receipt"}
        elif barrier == "uncertain":
            task.status = "uncertain"
        elif barrier == "canceled":
            run.status = "canceled"
        else:
            task.state = {"retries": run.policy["max_retries"]}
        db.commit()
    response = client.get(endpoint(ids, rid))
    assert response.json()["tasks"][0]["error"]["retryable"] is False
    response, _ = retry_request(client, ids, rid, tid)
    assert response.status_code == 409, response.text


def test_existing_provider_job_never_becomes_a_credit_retry(studio):
    client = studio[0]
    sessions, ids, rid, tid, jid, aid = prepared(studio, task_status="failed")
    with sessions() as db:
        task = db.get(ProductionTask, tid)
        # Even an incorrect legacy flag cannot bypass the durable job evidence.
        task.error = {"code": "QUOTA_EXCEEDED", "retryable": True}
        db.get(ProductionAttempt, aid).status = "failed"
        db.commit()
    response = client.get(endpoint(ids, rid))
    assert response.json()["tasks"][0]["error"]["retryable"] is False
    response, _ = retry_request(client, ids, rid, tid)
    assert response.status_code == 409
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert db.get(GenerationJob, jid).status == "pending"
