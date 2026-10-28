"""API-level proof of attach -> send -> saved catalog -> reuse, without paid calls."""

# ruff: noqa: F811
import uuid

import pytest
from sqlalchemy import func, select

from app.adapters.generation.mock import MockProvider
from app.core.config import settings
from app.models.agent import AgentRun
from app.models.attachment import Attachment
from app.models.conversation import ConversationMessage
from app.models.generation import GenerationJob
from app.models.harness import ProductionRun
from tests.test_conversation_attachments import attachments, upload  # noqa: F401
from tests.test_conversations import finish, message
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def new_conversation(client, ids):
    base = f"/api/v1/projects/{ids['project']}/conversations"
    row = post_ok(client, base, {})
    return f"{base}/{row['id']}"


@pytest.mark.parametrize("harness", [False, True])
def test_attachment_only_send_saved_once_and_pinned_for_model(attachments, monkeypatch, harness):
    client, sessions, ids = attachments
    monkeypatch.setattr(settings, "production_harness_enabled", harness)
    row = upload(client, ids)
    path = new_conversation(client, ids)
    payload = message("", attachments=[{"attachment_id": row["id"]}])
    sent = post_ok(client, path + "/messages", payload)
    assert len(sent["messages"]) == 2
    user, reply = sent["messages"]
    assert user["content"].startswith("请查看这些附件")
    descriptor = user["context"]["attachments"][0]
    assert descriptor["id"] == row["id"] and descriptor["blob_hash"] == row["blob_hash"]
    assert descriptor["reference"]["expected_blob_hash"] == row["blob_hash"]
    assert "chunk_hashes" not in descriptor
    assert len(post_ok(client, path + "/messages", payload)["messages"]) == 2
    root = f"/api/v1/projects/{ids['project']}"
    saved = client.get(root + "/library/materials?source=attachment").json()
    assert saved["total"] == 1 and saved["items"][0]["name"] == "参考"
    with sessions() as db:
        attachment = db.get(Attachment, uuid.UUID(row["id"]))
        assert str(attachment.message_id) == user["id"] and attachment.saved_at
        model = ProductionRun if harness else AgentRun
        run = db.get(model, uuid.UUID(reply["task_id"]))
        context = run.context if harness else run.input_context
        assert context["current_attachments"][0]["reference"]["id"] == row["id"]
        if harness:
            assert context["references"][0]["kind"] == "attachment"
        assert db.scalar(select(func.count()).select_from(ConversationMessage)) == 2
    # A new conversation reuses the fixed file and preserves the original provenance.
    another = new_conversation(client, ids)
    reused = post_ok(
        client,
        another + "/messages",
        message("继续使用这个参考", references=[{"attachment_id": row["id"]}]),
    )
    assert reused["messages"][0]["context"]["references"][0]["id"] == row["id"]
    assert client.get(root + "/library/materials?source=attachment").json()["total"] == 1
    assert client.get(root + "/attachments/" + row["id"]).json()["message_id"] == user["id"]


def test_failed_submit_keeps_draft_and_history_keeps_original(attachments, monkeypatch):
    client, sessions, ids = attachments
    monkeypatch.setattr(settings, "production_harness_enabled", False)
    row = upload(client, ids)
    path = new_conversation(client, ids)
    failed = client.post(
        path + "/messages",
        json=message(
            "按参考生成",
            mode="standard",
            attachments=[{"attachment_id": row["id"]}],
            media={"request_type": "image", "provider": "missing-provider"},
        ),
    )
    assert failed.status_code in {404, 409, 422}
    assert client.get(path).json()["messages"] == []
    root = f"/api/v1/projects/{ids['project']}"
    assert client.get(root + "/attachments/" + row["id"]).json()["saved_at"] is None
    post_ok(
        client,
        path + "/messages",
        message("看这个画面", attachments=[{"attachment_id": row["id"]}]),
    )
    from app.modules.agent import worker

    monkeypatch.setattr(worker, "SessionLocal", sessions)
    finish(client, path, "收到参考图。", monkeypatch)
    follow = post_ok(client, path + "/messages", message("上一张的名字是什么"))
    with sessions() as db:
        run = db.get(AgentRun, uuid.UUID(follow["messages"][-1]["task_id"]))
        assert run.input_context["current_attachments"] == []
        assert run.input_context["messages"][0]["attachments"][0]["blob_hash"] == row["blob_hash"]
    post_ok(client, root + f"/library/materials/attachment/{row['id']}/trash", {})
    assert client.get(root + "/library/materials?source=attachment").json()["total"] == 0
    assert (
        client.get(root + "/library/materials?source=attachment&deleted=true").json()["total"] == 1
    )
    assert client.get(root + "/attachments/" + row["id"]).json()["blob_hash"] == row["blob_hash"]


def test_generation_uses_uploaded_original_without_index_and_retries_same_input(
    attachments, monkeypatch
):
    client, sessions, ids = attachments
    monkeypatch.setattr(settings, "production_harness_enabled", False)
    from app.modules.generation import jobs

    caps = MockProvider.capabilities

    def supports_reference(self):
        value = caps(self)
        value.features.add("reference")
        value.max_reference_images = 8
        return value

    monkeypatch.setattr(MockProvider, "capabilities", supports_reference)
    monkeypatch.setattr(jobs, "dispatch", lambda db, job: job)
    row = upload(client, ids)
    path = new_conversation(client, ids)
    payload = message(
        "把参考图改成水彩",
        mode="standard",
        attachments=[{"attachment_id": row["id"]}],
        media={"provider": "mock", "request_type": "image"},
    )
    sent = post_ok(client, path + "/messages", payload)
    reply = sent["messages"][-1]
    with sessions() as db:
        job = db.get(GenerationJob, uuid.UUID(reply["task_id"]))
        assert job.input_snapshot["references"][0]["blob_hash"] == row["blob_hash"]
        assert job.input_snapshot["reference_sources"][0]["attachment_id"] == row["id"]
        job.status, job.error = "failed", "test failure before submission"
        db.commit()
    retry = client.get(path + f"/messages/{reply['id']}/retry-input").json()
    assert retry["attachments"][0]["attachment_id"] == row["id"]
    assert retry["media"]["references"] == []  # Do not duplicate merged references.
    assert len(post_ok(client, path + "/messages", payload)["messages"]) == 2


@pytest.mark.parametrize("phase", ["dispatching", "submission_unknown", "accepted"])
def test_chat_retry_never_resubmits_unknown_or_accepted_job(attachments, monkeypatch, phase):
    from app.modules.generation import jobs

    client, sessions, ids = attachments
    monkeypatch.setattr(settings, "production_harness_enabled", False)
    monkeypatch.setattr(jobs, "dispatch", lambda db, job: job)
    path = new_conversation(client, ids)
    reply = post_ok(
        client,
        path + "/messages",
        message("生成夜景", mode="standard", media={"provider": "mock", "request_type": "video"}),
    )["messages"][-1]
    with sessions() as db:
        job = db.get(GenerationJob, uuid.UUID(reply["task_id"]))
        job.status = "failed"
        job.routing_state = {"phase": phase, "provider_name": "mock", "external_job_id": "receipt"}
        job.input_snapshot = {
            **job.input_snapshot,
            "routing": {
                "candidates": [{"provider_name": "mock", "model_profile": {"protocol": "video_v1"}}]
            },
        }
        db.commit()
    endpoint = path + f"/messages/{reply['id']}"
    assert client.get(endpoint + "/retry-input").status_code == 409
    # Even after enabling the new harness, old tasks must retain receipt recovery.
    monkeypatch.setattr(settings, "production_harness_enabled", True)
    result = client.post(endpoint + "/retry", json={"request_key": str(uuid.uuid4())})
    assert result.status_code == (200 if phase == "accepted" else 409), result.text
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == (
            2 if phase == "accepted" else 1
        )
        if phase == "accepted":
            new = db.get(GenerationJob, uuid.UUID(result.json()["messages"][-1]["task_id"]))
            assert new.routing_state["resume"]["external_job_id"] == "receipt"
            assert new.input_snapshot["retry_of"] == reply["task_id"]


def test_saved_attachment_catalog_is_owner_scoped(attachments, monkeypatch):
    from app.core.security import create_access_token
    from app.models.identity import Membership, User

    client, sessions, ids = attachments
    monkeypatch.setattr(settings, "production_harness_enabled", False)
    row = upload(client, ids)
    path = new_conversation(client, ids)
    post_ok(
        client,
        path + "/messages",
        message("这是私有参考", attachments=[{"attachment_id": row["id"]}]),
    )
    with sessions() as db:
        user = User(
            username="attachment_other",
            email="attachment_other@example.com",
            password_hash="unused",
            display_name="Other",
        )
        db.add(user)
        db.flush()
        db.add(Membership(project_id=uuid.UUID(ids["project"]), user_id=user.id, role="admin"))
        uid = user.id
        db.commit()
    client.headers["Authorization"] = f"Bearer {create_access_token(str(uid))}"
    root = f"/api/v1/projects/{ids['project']}"
    assert client.get(root + "/library/materials?source=attachment").json()["total"] == 0
    another = new_conversation(client, ids)
    assert (
        client.post(
            another + "/messages",
            json=message("读取别人的参考", attachments=[{"attachment_id": row["id"]}]),
        ).status_code
        == 404
    )
    assert client.get(another).json()["messages"] == []


def test_new_guidance_demotes_old_media_to_optional_context(attachments, monkeypatch):
    from app.models.harness import ProductionTask
    from app.modules.harness import runtime, service
    from app.modules.harness.context import build_context
    from tests.test_conversation_attachments import context

    client, sessions, ids = attachments
    monkeypatch.setattr(settings, "production_harness_enabled", True)
    original = upload(client, ids)
    ids_to_send = []
    with sessions() as db:
        first = db.get(Attachment, uuid.UUID(original["id"]))
        for i in range(16):
            item = Attachment(
                project_id=first.project_id,
                created_by=first.created_by,
                request_key=uuid.uuid4(),
                title=f"参考 {i}",
                filename=first.filename,
                kind=first.kind,
                fingerprint=first.fingerprint,
                size_bytes=first.size_bytes,
                uploaded_bytes=first.size_bytes,
                status="ready",
                blob_hash=first.blob_hash,
                width=first.width,
                height=first.height,
            )
            db.add(item)
            db.flush()
            ids_to_send.append(str(item.id))
        db.commit()
    path = new_conversation(client, ids)
    sent = None
    for batch in (ids_to_send[:8], ids_to_send[8:]):
        sent = post_ok(
            client,
            path + "/messages",
            message("参考这些画面", attachments=[{"attachment_id": v} for v in batch]),
        )
    with sessions() as db:
        run = db.get(ProductionRun, uuid.UUID(sent["messages"][-1]["task_id"]))
        assert len(run.context["references"]) == 16
        assert sum(bool(r["required"]) for r in run.context["references"]) == 8
        # Default 12-reference model budget can still build a context packet.
        build_context(db, context(db, ids), goal="继续创作", references=run.context["references"])
        business = db.scalar(
            select(ProductionTask).where(
                ProductionTask.run_id == run.id,
                ProductionTask.role == "coordinator",
            )
        )
        assert {r["id"] for r in business.specification["references"]} == set(ids_to_send[:8])
        assert all(r["required"] for r in business.specification["references"])
        ticket = service.claim(db, run.id)
        db.commit()
        impact = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        assert impact.role == "impact"
        assert {r["id"] for r in impact.specification["references"] if r["required"]} == set(
            ids_to_send[8:]
        )
        prepared = runtime.prepare(db, ticket)
        assert {r["id"] for r in prepared["current_references"]} == set(ids_to_send[8:])


def test_attached_followup_preserves_guidance_anchor_and_request_idempotency(
    attachments, monkeypatch
):
    from app.models.harness import ProductionTask
    from app.modules.harness import guidance, runtime, service
    from tests.test_attachment_model_context import seed
    from tests.test_conversation_attachments import context

    client, sessions, ids = attachments
    monkeypatch.setattr(settings, "production_harness_enabled", True)
    first = upload(client, ids)
    path = new_conversation(client, ids)
    sent = post_ok(
        client,
        path + "/messages",
        message("构思这张图的开场", attachments=[{"attachment_id": first["id"]}]),
    )
    run_id = uuid.UUID(sent["messages"][-1]["task_id"])
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        business = service.tasks(db, run)[0]
        business_id, fixed_spec = business.id, business.specification.copy()
        guidance.receive(
            db,
            context(db, ids),
            run.id,
            uuid.uuid4(),
            "只改开场的光线",
            anchor={"task_ids": [str(business.id)], "references": run.context["references"]},
        )
        inherited = run.context["guidance_inbox"][-1]["anchor"]
        second = seed(db, ids)
        db.commit()
    payload = message("光线参考这张", attachments=[{"attachment_id": second["id"]}])
    once = post_ok(client, path + "/messages", payload)
    repeated = post_ok(client, path + "/messages", payload)
    assert len(once["messages"]) == len(repeated["messages"]) == 4
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        latest = run.context["guidance_inbox"][-1]
        assert latest["anchor"]["task_ids"] == inherited["task_ids"] == [str(business_id)]
        assert latest["anchor"]["references"] == inherited["references"]
        assert len(run.context["guidance_inbox"]) == 2
        assert db.get(ProductionTask, business_id).specification == fixed_spec
        ticket = service.claim(db, run_id)
        db.commit()
        impact = db.get(ProductionTask, uuid.UUID(ticket["task_id"]))
        assert {ref["id"] for ref in impact.specification["references"]} == {
            first["id"],
            second["id"],
        }
        packet = runtime.prepare(db, ticket)
        assert {ref["id"] for ref in packet["current_references"]} == {second["id"]}
