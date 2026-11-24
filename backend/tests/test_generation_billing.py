"""Real PostgreSQL wallet and generation executor, with offline upstreams."""

# ruff: noqa: F811
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.adapters.contracts import GenerationResult, Output
from app.adapters.generation.mock import MockProvider
from app.models.billing import BillingSettings, CreditAccount, CreditEntry, CreditReservation
from app.models.generation import GenerationJob
from app.modules.billing import wallet
from app.modules.generation import jobs, model_routing, service
from app.modules.generation.model_routing import SubmissionRejected
from tests.test_creative_models import group
from tests.test_media_workflow import studio  # noqa: F401
from tests.test_model_channels import add, setup
from tests.test_platform_models import ROOT, shared


def configure(studio, *, amount="1.25", credits=1000, enabled=True, direct=False):
    client, sessions, ids = studio
    channel = shared(client, sessions, ids)
    options = {"price": {"mode": "per_request", "amount": amount}} if amount else {}
    profile = add(client, ROOT, channel, protocol="mock", **options)
    model = profile if direct else group(client, ROOT, [profile])
    with sessions() as db:
        db.add(BillingSettings(id=1, payment_required=enabled))
        wallet.change(db, ids["user"], "test_funding", uuid.uuid4(), available=credits)
        db.commit()
    return model


def submit(studio, model):
    client, _, ids = studio
    return client.post(
        f"/api/v1/projects/{ids['project']}/shots/{ids['shot']}/generate",
        json={"provider": model["provider_name"]},
    )


def account(studio):
    with studio[1]() as db:
        row = db.get(CreditAccount, studio[2]["user"])
        return row.available, row.reserved


def retry(studio, job_id):
    return studio[0].post(f"/api/v1/projects/{studio[2]['project']}/jobs/{job_id}/retry")


@pytest.mark.parametrize("direct", [True, False])
def test_platform_settles_once_with_outputs(studio, direct):
    response = submit(studio, configure(studio, direct=direct))
    assert response.status_code == 200 and response.json()["status"] == "succeeded", response.text
    assert account(studio) == (875, 0)
    job_id = uuid.UUID(response.json()["id"])
    with studio[1]() as db:
        jobs.execute(db, job_id)
        reservation = db.get(CreditReservation, job_id)
        assert reservation.status == "settled" and reservation.settled_units == 125
        entries = list(
            db.scalars(
                select(CreditEntry).where(
                    CreditEntry.kind == "generation_settle",
                    CreditEntry.reference_id == job_id,
                )
            )
        )
        assert len(entries) == 1


def test_insufficient_balance_is_atomic_and_precedes_provider(studio, monkeypatch):
    model = configure(studio, credits=100)
    monkeypatch.setattr(MockProvider, "submit", lambda *a: pytest.fail("must not submit"))
    response = submit(studio, model)
    assert response.status_code == 402, response.text
    assert account(studio) == (100, 0)
    with studio[1]() as db:
        assert not list(db.scalars(select(GenerationJob)))


@pytest.mark.parametrize("amount", [None, "0"])
def test_unpriced_platform_is_not_free_in_paid_mode(studio, monkeypatch, amount):
    model = configure(studio, amount=amount)
    monkeypatch.setattr(MockProvider, "submit", lambda *a: pytest.fail("must not submit"))
    response = submit(studio, model)
    assert response.status_code == 422, response.text
    assert account(studio) == (1000, 0)


def test_disabled_mode_and_project_credentials_preserve_legacy(studio):
    model = configure(studio, enabled=False)
    response = submit(studio, model)
    assert response.json()["status"] == "succeeded", response.text
    client, sessions, ids = studio
    with sessions() as db:
        db.get(BillingSettings, 1).payment_required = True
        db.commit()
    root, channel = setup(client, ids["project"])
    profile = add(
        client, root, channel, protocol="mock", price={"mode": "per_request", "amount": "9"}
    )
    response = submit(studio, group(client, root, [profile]))
    assert response.json()["status"] == "succeeded", response.text
    assert account(studio) == (1000, 0)


def test_queued_cancel_releases_and_never_executes(studio, monkeypatch):
    model = configure(studio)
    monkeypatch.setattr(jobs, "dispatch", lambda db, job: job)
    response = submit(studio, model)
    assert response.status_code == 200, response.text
    assert account(studio) == (875, 125)
    job_id = response.json()["id"]
    canceled = studio[0].post(f"/api/v1/projects/{studio[2]['project']}/jobs/{job_id}/cancel")
    assert canceled.status_code == 200, canceled.text
    assert account(studio) == (1000, 0)
    monkeypatch.setattr(MockProvider, "submit", lambda *a: pytest.fail("must not submit"))
    with studio[1]() as db:
        jobs.execute(db, uuid.UUID(job_id))


def test_local_failure_releases_and_network_uncertainty_holds(studio, monkeypatch):
    model = configure(studio)
    original = service._materialize_references

    def unavailable(*args):
        raise ValueError("local input unavailable")

    monkeypatch.setattr(service, "_materialize_references", unavailable)
    first = submit(studio, model)
    assert first.json()["status"] == "failed", first.text
    assert account(studio) == (1000, 0)
    monkeypatch.setattr(service, "_materialize_references", original)

    def timeout(*args):
        raise TimeoutError("upstream may have accepted")

    monkeypatch.setattr(MockProvider, "submit", timeout)
    second = submit(studio, model)
    assert second.json()["status"] == "failed", second.text
    assert account(studio) == (875, 125)
    assert retry(studio, second.json()["id"]).status_code == 409


def test_rejected_submission_releases_and_fresh_retry_reserves(studio, monkeypatch):
    model = configure(studio)
    original = MockProvider.submit

    def rejected(*args):
        raise SubmissionRejected("definitely rejected")

    monkeypatch.setattr(MockProvider, "submit", rejected)
    response = submit(studio, model)
    assert response.json()["status"] == "failed", response.text
    assert account(studio) == (1000, 0)
    monkeypatch.setattr(MockProvider, "submit", original)
    response = retry(studio, response.json()["id"])
    assert response.status_code == 200 and response.json()["status"] == "succeeded", response.text
    assert account(studio) == (875, 0)


def test_resume_chain_never_resubmits_or_double_reserves(studio, monkeypatch):
    model = configure(studio)
    monkeypatch.setattr(
        model_routing, "RESUMABLE_PROTOCOLS", model_routing.RESUMABLE_PROTOCOLS | {"mock"}
    )
    original = MockProvider.poll

    def timeout(*args):
        raise TimeoutError("receipt unavailable")

    monkeypatch.setattr(MockProvider, "poll", timeout)
    response = submit(studio, model)
    assert response.json()["status"] == "failed", response.text
    job_id = response.json()["id"]
    assert account(studio) == (875, 125)
    monkeypatch.setattr(MockProvider, "poll", original)
    monkeypatch.setattr(MockProvider, "submit", lambda *a: pytest.fail("must resume old upstream"))
    response = retry(studio, job_id)
    assert response.status_code == 200 and response.json()["status"] == "succeeded", response.text
    assert account(studio) == (875, 0)
    assert retry(studio, job_id).status_code == 409
    with studio[1]() as db:
        assert len(list(db.scalars(select(CreditReservation)))) == 1
        assert db.get(CreditReservation, uuid.UUID(job_id)).status == "settled"


@pytest.mark.parametrize("direct", [False, True])
def test_confirmed_terminal_receipt_returns_original_hold_once(studio, monkeypatch, direct):
    model = configure(studio, direct=direct)
    failed = GenerationResult.confirmed_failure(
        "输入未通过内容审核（MiniMax 1026）", kind="input_moderation", code="1026"
    )
    original_poll = MockProvider.poll
    monkeypatch.setattr(MockProvider, "poll", lambda *_: failed)
    response = submit(studio, model)
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "failed" and "1026" in job["error"]
    assert job["routing_state"]["failure"] == {"kind": "input_moderation", "code": "1026"}
    assert job["credit_reservation"] == {"status": "released", "points": "1.25"}
    assert account(studio) == (1000, 0)
    job_id = uuid.UUID(job["id"])
    with studio[1]() as db:
        jobs.execute(db, job_id)
        entries = db.scalars(select(CreditEntry.kind).where(CreditEntry.reference_id == job_id))
        assert sorted(entries) == ["generation_release", "generation_reserve"]
    monkeypatch.setattr(MockProvider, "poll", original_poll)
    response = retry(studio, job_id)
    assert response.status_code == 200 and response.json()["status"] == "succeeded", response.text
    assert account(studio) == (875, 0)


@pytest.mark.parametrize(
    "result",
    [
        GenerationResult(status="failed", error="图片数据不可读"),
        GenerationResult(status="succeeded", outputs=[Output(data=b"")]),
        GenerationResult(
            status="failed",
            error="回执矛盾",
            failure_kind="provider_failure",
            outputs=[Output(data=b"partial")],
        ),
    ],
)
def test_unverified_outcome_keeps_hold_and_displays_current_ledger(studio, monkeypatch, result):
    model = configure(studio)
    monkeypatch.setattr(MockProvider, "poll", lambda *_: result)
    job = submit(studio, model).json()
    assert job["status"] == "failed"
    assert job["routing_state"]["phase"] in ("accepted", "generated")
    assert job["credit_reservation"] == {"status": "reserved", "points": "1.25"}
    assert account(studio) == (875, 125)
    with studio[1]() as db:
        wallet.release_generation(db, uuid.UUID(job["id"]))
        db.commit()
    current = studio[0].get(f"/api/v1/projects/{studio[2]['project']}/jobs/{job['id']}").json()
    assert current["credit_reservation"] == {"status": "released", "points": "1.25"}


def test_confirmed_failure_during_receipt_recovery_releases_shared_hold(studio, monkeypatch):
    model = configure(studio)
    monkeypatch.setattr(
        model_routing, "RESUMABLE_PROTOCOLS", model_routing.RESUMABLE_PROTOCOLS | {"mock"}
    )
    original_poll = MockProvider.poll
    original_submit = MockProvider.submit

    def interrupted(*_):
        raise TimeoutError("receipt temporarily unavailable")

    monkeypatch.setattr(MockProvider, "poll", interrupted)
    first = submit(studio, model).json()
    assert first["credit_reservation"]["status"] == "reserved"
    monkeypatch.setattr(
        MockProvider, "submit", lambda *_: pytest.fail("must only query the original receipt")
    )
    monkeypatch.setattr(
        MockProvider,
        "poll",
        lambda *_: GenerationResult.confirmed_failure("供应商已取消生成", kind="provider_canceled"),
    )
    recovered = retry(studio, first["id"]).json()
    assert recovered["status"] == "failed"
    assert account(studio) == (1000, 0)
    with studio[1]() as db:
        reservations = list(db.scalars(select(CreditReservation)))
        assert [(r.job_id, r.status) for r in reservations] == [
            (uuid.UUID(first["id"]), "released")
        ]
    monkeypatch.setattr(MockProvider, "submit", original_submit)
    monkeypatch.setattr(MockProvider, "poll", original_poll)
    replacement = retry(studio, recovered["id"])
    assert replacement.status_code == 200 and replacement.json()["status"] == "succeeded", (
        replacement.text
    )
    assert account(studio) == (875, 0)


def test_enabling_payment_checks_old_pending_job_before_network(studio, monkeypatch):
    model = configure(studio, credits=0, enabled=False)
    monkeypatch.setattr(jobs, "dispatch", lambda db, job: job)
    response = submit(studio, model)
    assert response.status_code == 200, response.text
    with studio[1]() as db:
        db.get(BillingSettings, 1).payment_required = True
        db.commit()
        monkeypatch.setattr(MockProvider, "submit", lambda *a: pytest.fail("must not submit"))
        jobs.execute(db, uuid.UUID(response.json()["id"]))
        assert db.get(GenerationJob, uuid.UUID(response.json()["id"])).status == "failed"
    assert account(studio) == (0, 0)


@pytest.mark.parametrize("accepted", [True, False])
def test_cancel_during_submit_waits_for_the_upstream_outcome(studio, monkeypatch, accepted):
    model = configure(studio)
    original = MockProvider.submit
    _, sessions, ids = studio

    def cancel_before_receipt(provider, request):
        with sessions() as db:
            job = db.scalar(select(GenerationJob).where(GenerationJob.status == "running"))
            ctx = SimpleNamespace(
                project=SimpleNamespace(id=uuid.UUID(ids["project"])),
                user=SimpleNamespace(id=ids["user"]),
                role="admin",
            )
            jobs.cancel(db, ctx, job.id)
            db.commit()
        assert account(studio) == (875, 125)
        if not accepted:
            raise SubmissionRejected("供应商已拒绝这次提交")
        return original(provider, request)

    monkeypatch.setattr(MockProvider, "submit", cancel_before_receipt)
    response = submit(studio, model)
    assert response.status_code == 200 and response.json()["status"] == "canceled", response.text
    assert account(studio) == ((875, 125) if accepted else (1000, 0))
    with sessions() as db:
        row = db.get(GenerationJob, uuid.UUID(response.json()["id"]))
        assert row.cost_raw["billing"]["phase"] == ("held" if accepted else "released")
        assert db.get(CreditReservation, row.id).status == ("reserved" if accepted else "released")


def test_settles_actual_cheaper_route_and_returns_unused_hold(studio, monkeypatch):
    model = configure(studio)
    client, _, ids = studio
    # Obtain the first existing profile from the model's frozen route definition.
    with studio[1]() as db:
        from app.models.generation import CreativeModel, ProviderConfig

        row = db.get(CreativeModel, uuid.UUID(model["id"]))
        profile = db.get(ProviderConfig, uuid.UUID(row.definition["routes"][0]["profile_id"]))
        first = {"id": str(profile.id)}
        channel = profile.config["channel_id"]
    cheap = add(
        client,
        ROOT,
        channel,
        protocol="mock",
        model="cheaper",
        price={"mode": "per_request", "amount": "0.50"},
    )
    routed = group(client, ROOT, [first, cheap], name="fallback-price")
    original = MockProvider.submit
    calls = []

    def submit_once(provider, request):
        calls.append(provider._cfg["config"].get("model"))
        if len(calls) == 1:
            raise SubmissionRejected("definitely rejected first route")
        return original(provider, request)

    monkeypatch.setattr(MockProvider, "submit", submit_once)
    response = submit(studio, routed)
    assert response.status_code == 200 and response.json()["status"] == "succeeded", response.text
    assert account(studio) == (950, 0)
