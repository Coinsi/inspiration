"""Real PostgreSQL wallet and generation executor, with offline upstreams."""

# ruff: noqa: F811
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

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


def test_cancel_while_submit_is_in_flight_keeps_hold(studio, monkeypatch):
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
        return original(provider, request)

    monkeypatch.setattr(MockProvider, "submit", cancel_before_receipt)
    response = submit(studio, model)
    assert response.status_code == 200 and response.json()["status"] == "canceled", response.text
    assert account(studio) == (875, 125)
    with sessions() as db:
        row = db.get(GenerationJob, uuid.UUID(response.json()["id"]))
        assert row.cost_raw["billing"]["phase"] == "held"
        assert db.get(CreditReservation, row.id).status == "reserved"


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
