"""Unified chat projections, guidance and direct media without paid model calls."""

# ruff: noqa: F811
import copy
import uuid

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.errors import Conflict, NotFound
from app.models.conversation import Conversation, ConversationMessage
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionRun, ProductionTask
from app.models.skill import CreativeSkill, CreativeSkillVersion
from app.modules.conversation import production
from app.modules.conversation.schemas import Control, Send
from app.modules.generation import media_engine
from app.modules.harness import guidance, kernel, runtime, schemas, service
from app.storage import cas
from tests.test_harness_context import context
from tests.test_media_workflow import post_ok, studio  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_kernel(monkeypatch):
    monkeypatch.setattr(settings, "production_harness_enabled", True)
    monkeypatch.setattr(kernel, "_KERNELS", dict(kernel._KERNELS))
    kernel._KERNELS["mock"] = lambda *_: {"kind": "finish", "message": "离线测试完成"}


def conversation(db, ids):
    row = Conversation(
        project_id=uuid.UUID(ids["project"]), created_by=ids["user"], title="持续创作"
    )
    db.add(row)
    db.flush()
    return row


def start(db, ids, row, *, ordinal=2, history=None, **kw):
    data = Send(
        content=kw.pop("content", "做一个雨夜的开场"),
        request_key=kw.pop("request_key", uuid.uuid4()),
        mode=kw.pop("mode", "test"),
        **kw,
    )
    message = ConversationMessage(
        conversation_id=row.id,
        role="assistant",
        ordinal=ordinal,
        request_key=data.request_key,
        content="",
        status="queued",
    )
    result = production.start(
        db,
        context(db, ids),
        row,
        message,
        data,
        history or {"messages": [], "history_truncated": False},
    )
    db.add(message)
    db.flush()
    return message, db.get(ProductionRun, result["id"]), data


def test_history_saved_in_run_and_separate_conversations_never_share_request(studio):
    _, sessions, ids = studio
    with sessions() as db:
        c = conversation(db, ids)
        history = {
            "messages": [
                {"id": "user-1", "role": "user", "content": "主角穿黑衣"},
                {"id": "assistant-1", "role": "assistant", "content": "雨夜开场"},
            ],
            "history_truncated": True,
        }
        message, run, data = start(db, ids, c, history=history)
        assert run.context["history"] == history["messages"]
        assert run.context["history_truncated"] and message.context["history_truncated"]
        assert message.execution_kind == "production"
        assert run.context["facts"] == []  # quoted history isn't confirmed decision state
        db.commit()
        cid, rid = c.id, run.id
    with sessions() as db:
        c = db.get(Conversation, cid)
        scratch = ConversationMessage(conversation_id=cid, ordinal=99, role="assistant")
        production.start(
            db,
            context(db, ids),
            c,
            scratch,
            data,
            {"messages": [{"role": "user", "content": "later history"}]},
        )
        assert scratch.execution_id == rid
        assert db.get(ProductionRun, rid).context["history"] == history["messages"]
        with pytest.raises(Conflict):
            production.start(
                db,
                context(db, ids),
                c,
                scratch,
                data.model_copy(update={"content": "不能偷偷换要求"}),
                history,
            )
        other = conversation(db, ids)
        _, other_run, _ = start(db, ids, other, request_key=data.request_key)
        assert other_run.id != rid
        assert other_run.context["history"] == []


def test_new_turn_does_not_cancel_running_work_and_explicit_guidance_fences_old_attempt(studio):
    _, sessions, ids = studio
    with sessions() as db:
        c = conversation(db, ids)
        old, run, _ = start(db, ids, c)
        ticket = service.claim(db, run.id)
        first_id = uuid.UUID(ticket["task_id"])
        _, continuation, _ = start(db, ids, c, ordinal=4, content="同时讨论一下配乐")
        assert db.get(ProductionTask, first_id).status == "running"
        assert continuation.id == run.id
        impact = service.claim(db, run.id)
        guidance.resolve(
            db,
            impact,
            guidance.Decision(
                guidance_id=guidance.pending(run),
                action="discussion",
                message="可以先讨论配乐。",
            ),
        )
        revised, same, _ = start(
            db,
            ids,
            c,
            ordinal=6,
            content="开场换成白天，其他不动",
            production={
                "run_id": run.id,
                "action": "steer",
                "expected_revision": run.revision,
                "task_ids": [first_id],
            },
        )
        assert same.id == run.id and run.revision == 3
        assert db.get(ProductionTask, first_id).status == "superseded"
        assert old.status == "completed"
        assert old.context["production"]["is_current"] is False
        preserved = (old.content, copy.deepcopy(old.context), copy.deepcopy(old.progress))
        assert (
            service.finish_attempt(db, ticket, status="succeeded", result={"message": "旧结果"})
            is False
        )
        active = [
            t
            for t in service.tasks(db, run)
            if t.status != "superseded" and not guidance.is_impact(t)
        ]
        active[0].status = "succeeded"
        active[0].result = {"message": "已按白天重新准备"}
        db.flush()
        service.reconcile(db, run)
        production.publish(db, run)
        assert revised.content == "已按白天重新准备"
        assert (old.content, old.context, old.progress) == preserved
        with pytest.raises(Conflict):
            production.control(db, context(db, ids), old, Control(action="pause"))


def test_guidance_cannot_attach_to_other_conversation_or_stale_revision(studio):
    _, sessions, ids = studio
    with sessions() as db:
        c = conversation(db, ids)
        _, run, _ = start(db, ids, c)
        other = conversation(db, ids)
        with pytest.raises(NotFound):
            start(
                db,
                ids,
                other,
                production={"run_id": run.id, "action": "steer", "expected_revision": 1},
            )
        assert run.revision == 1
        with pytest.raises(Conflict):
            start(
                db,
                ids,
                c,
                ordinal=4,
                production={"run_id": run.id, "action": "steer", "expected_revision": 9},
            )
        assert run.revision == 1


def test_plan_confirmation_and_question_reply_share_same_production(studio):
    _, sessions, ids = studio
    with sessions() as db:
        c = conversation(db, ids)
        initial, run, _ = start(db, ids, c)
        coordinator = service.tasks(db, run)[0]
        coordinator.status = "waiting_plan"
        run.status = "needs_plan_approval"
        run.plan = schemas.Plan(
            summary="先整理剧情，再制作画面",
            tasks=[schemas.TaskSpec(key="story", goal="整理剧情")],
        ).model_dump(mode="json")
        db.flush()
        production.publish(db, run)
        assert initial.status == "needs_review"
        assert initial.context["production"]["plan"]["summary"] == run.plan["summary"]
        confirmed, _, _ = start(
            db,
            ids,
            c,
            ordinal=4,
            content="确认，继续",
            production={"run_id": run.id, "action": "approve_plan", "expected_revision": 1},
        )
        children = [t for t in service.tasks(db, run) if t.parent_id == coordinator.id]
        assert len(children) == 1
        child = children[0]
        child.status = "waiting_input"
        child.state = {**child.state, "question": "配乐偏悬疑还是舒缓？"}
        child.result = {"message": child.state["question"]}
        run.status = "needs_attention"
        db.flush()
        production.publish(db, run)
        assert confirmed.context["production"]["questions"][0]["task_id"] == str(child.id)
        answered, _, _ = start(
            db,
            ids,
            c,
            ordinal=6,
            content="悬疑一些",
            production={
                "run_id": run.id,
                "action": "reply",
                "expected_revision": run.revision,
                "task_ids": [child.id],
            },
        )
        assert child.state["answer"] == "悬疑一些" and child.status == "queued"
        assert answered.execution_id == initial.execution_id
        assert confirmed.context["production"]["is_current"] is False


def test_controls_pause_resume_cancel_a_specific_run_without_touching_other_work(studio):
    _, sessions, ids = studio
    with sessions() as db:
        c = conversation(db, ids)
        first, run, _ = start(db, ids, c)
        _, other, _ = start(db, ids, conversation(db, ids))
        production.control(db, context(db, ids), first, Control(action="pause"))
        assert run.status == "paused" and first.status == "paused"
        assert other.status == "queued"
        production.control(db, context(db, ids), first, Control(action="resume"))
        assert first.status in {"queued", "running"}
        production.control(db, context(db, ids), first, Control(action="cancel"))
        assert first.status == "cancelled" and other.status == "queued"
        assert production.read(db, context(db, ids), first)["execution_kind"] == "production"


def test_fixed_skill_version_is_not_replaced_by_newer_revision(studio):
    _, sessions, ids = studio
    with sessions() as db:
        skill = CreativeSkill(
            project_id=uuid.UUID(ids["project"]),
            created_by=ids["user"],
            name="镜头方法",
            revision=2,
            document={"name": "镜头方法", "instructions": "新版"},
        )
        db.add(skill)
        db.flush()
        db.add_all(
            [
                CreativeSkillVersion(
                    skill_id=skill.id,
                    revision=i,
                    created_by=ids["user"],
                    document={"name": "镜头方法", "instructions": text},
                )
                for i, text in [(1, "原本选定的方法"), (2, "新版")]
            ]
        )
        db.flush()
        _, run, _ = start(db, ids, conversation(db, ids), skills=[{"id": skill.id, "revision": 1}])
        assert run.context["skills"][0]["revision"] == 1
        assert run.context["skills"][0]["instructions"] == "原本选定的方法"


def test_direct_generation_uses_same_harness_and_does_not_dispatch_during_send(studio):
    _, sessions, ids = studio
    with sessions() as db:
        message, run, _ = start(
            db,
            ids,
            conversation(db, ids),
            mode="standard",
            model_provider="unconfigured-text",
            media={"provider": "mock", "request_type": "image"},
        )
        task = service.tasks(db, run)[0]
        assert task.kind == "tool" and task.specification["tool"] == "media.generate"
        assert task.specification["expected_output"] == "artifact"
        assert run.context["direct_request"] and run.context["estimate"]["submitted"] is False
        assert run.status in {"queued", "running"}
        assert run.policy["budget_points"] == 10 and "generate" in run.policy["effects"]
        assert message.execution_kind == "production"
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0
        assert task.specification["arguments"]["provider"] == "mock"


def test_completed_media_is_visible_and_followup_receives_fixed_reference_not_fake_artifact(studio):
    _, sessions, ids = studio
    with sessions() as db:
        c = conversation(db, ids)
        message, run, _ = start(db, ids, c)
        task = service.tasks(db, run)[0]
        blob = cas.put_bytes(db, media_engine.mock_image(), "image/png", width=512, height=512)
        job = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_type="shot",
            target_id=uuid.UUID(ids["shot"]),
            provider="mock",
            request_type="image",
            status="succeeded",
            input_snapshot={},
            created_by=ids["user"],
        )
        db.add(job)
        db.flush()
        generation = Generation(
            project_id=uuid.UUID(ids["project"]),
            job_id=job.id,
            target_type="shot",
            target_id=uuid.UUID(ids["shot"]),
            provider="mock",
            output_type="image",
            output_blob_hash=blob.hash,
        )
        db.add(generation)
        db.flush()
        receipt = {
            "status": "succeeded",
            "tool": "media.generate",
            "data": {"job_id": str(job.id), "outputs": [{"id": "model-made-up"}]},
        }
        task.status = "succeeded"
        task.result = {
            "message": "画面已生成",
            "receipts": [receipt],
            "suggested_artifacts": [{"id": "not-an-actual-generation"}],
        }
        db.flush()
        service.reconcile(db, run)
        production.publish(db, run)
        assert [o["id"] for o in message.context["outputs"]] == [str(generation.id)]
        assert message.status == "completed"
        _, followup, _ = start(
            db,
            ids,
            c,
            ordinal=4,
            content="把上张图片的光线调暗",
            history={
                "messages": [
                    {
                        "role": "assistant",
                        "content": message.content,
                        "media_outputs": message.context["outputs"],
                    }
                ],
                "history_truncated": False,
            },
        )
        refs = followup.context["references"]
        assert len(refs) == 1 and refs[0]["id"] == str(generation.id)
        assert refs[0]["expected_blob_hash"] == blob.hash
        assert "content_parts" not in followup.context
        assert followup.context["facts"] == []


def test_failed_and_uncertain_tasks_surface_error_and_safe_retryability(studio):
    _, sessions, ids = studio
    with sessions() as db:
        c = conversation(db, ids)
        message, run, _ = start(db, ids, c)
        task = service.tasks(db, run)[0]
        task.status = "uncertain"
        task.error = {"message": "上游提交结果未知，需要核对", "retryable": False}
        db.flush()
        service.reconcile(db, run)
        production.sync(db, c.id)
        assert message.status == "needs_review"
        assert "核对" in message.error
        assert message.context["production"]["retryable"] is False
        task.status = "failed"
        task.error = {"message": "输入已变化，请刷新", "retryable": True}
        db.flush()
        service.reconcile(db, run)
        production.publish(db, run)
        assert message.status == "failed" and message.context["production"]["retryable"]


def api_conversation(studio):
    client, sessions, ids = studio
    base = f"/api/v1/projects/{ids['project']}/conversations"
    row = post_ok(client, base, {})
    return client, sessions, ids, f"{base}/{row['id']}"


def test_default_http_route_continues_dialogue_during_execution_and_guidance_is_idempotent(studio):
    client, sessions, ids, path = api_conversation(studio)
    payload = {"request_key": str(uuid.uuid4()), "content": "先写雨夜开场", "mode": "test"}
    first = post_ok(client, path + "/messages", payload)
    reply = first["messages"][-1]
    run_id = uuid.UUID(reply["task_id"])
    assert reply["execution_kind"] == "production"
    assert reply["task_path"].startswith("workbench?conversation=")
    assert f"message={reply['id']}" in reply["task_path"]
    assert len(post_ok(client, path + "/messages", payload)["messages"]) == 2
    assert (
        client.post(path + "/messages", json={**payload, "content": "不同要求"}).status_code == 409
    )
    next_payload = {**payload, "request_key": str(uuid.uuid4()), "content": "我们也讨论一下音乐"}
    later = post_ok(client, path + "/messages", next_payload)
    assert len(later["messages"]) == 4 and later["messages"][-1]["task_id"] == str(run_id)
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        impact = service.claim(db, run_id)
        guidance.resolve(
            db,
            impact,
            guidance.Decision(
                guidance_id=guidance.pending(run),
                action="discussion",
                message="可以先讨论配乐。",
            ),
        )
        task = service.tasks(db, run)[0]
        guide = {
            **payload,
            "request_key": str(uuid.uuid4()),
            "content": "把开场改成清晨",
            "production": {
                "run_id": str(run_id),
                "expected_revision": run.revision,
                "action": "steer",
                "task_ids": [str(task.id)],
            },
        }
        db.commit()
    guided = post_ok(client, path + "/messages", guide)
    assert len(guided["messages"]) == 6
    assert guided["messages"][-1]["task_id"] == str(run_id)
    assert guided["messages"][1]["context"]["production"]["is_current"] is False
    duplicate = post_ok(client, path + "/messages", guide)
    assert len(duplicate["messages"]) == 6
    stale = client.post(path + "/messages", json={**guide, "request_key": str(uuid.uuid4())})
    assert stale.status_code == 409
    assert len(client.get(path).json()["messages"]) == 6
    current = guided["messages"][-1]
    control = f"{path}/messages/{current['id']}/control"
    assert post_ok(client, control, {"action": "pause"})["status"] == "paused"
    assert post_ok(client, control, {"action": "resume"})["status"] in {"queued", "running"}
    with sessions() as db:
        ticket = service.claim(db, run_id)
        db.commit()
    runtime.execute(ticket, sessions)
    final = client.get(path).json()
    assert final["messages"][-1]["status"] == "completed"
    assert final["messages"][-1]["content"] == "离线测试完成"
    assert (
        final["messages"][3]["status"] == "completed"
    )  # prior guidance remains an immutable snapshot


def test_http_retry_never_creates_another_generation_for_uncertain_submission(studio):
    client, sessions, ids, path = api_conversation(studio)
    data = post_ok(
        client,
        path + "/messages",
        {
            "request_key": str(uuid.uuid4()),
            "content": "制作画面",
            "mode": "test",
        },
    )
    reply = data["messages"][-1]
    run_id = uuid.UUID(reply["task_id"])
    with sessions() as db:
        run = db.get(ProductionRun, run_id)
        task = service.tasks(db, run)[0]
        task.status = "failed"
        task.error = {"message": "上游提交结果待核对", "retryable": False}
        run.status = "failed"
        production.publish(db, run)
        db.commit()
    path_retry = f"{path}/messages/{reply['id']}/retry"
    assert client.post(path_retry, json={"request_key": str(uuid.uuid4())}).status_code == 409
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(ProductionRun)) == 1
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0
        run = db.get(ProductionRun, run_id)
        task = service.tasks(db, run)[0]
        task.error = {"message": "输入需要刷新", "retryable": True}
        db.commit()
    retried = post_ok(client, path_retry, {"request_key": str(uuid.uuid4())})
    assert len(retried["messages"]) == 2
    assert retried["messages"][-1]["task_id"] == str(run_id)
    assert retried["messages"][-1]["status"] in {"queued", "running"}


def test_http_ambiguous_multi_run_requires_target_and_explicit_guidance_uses_same_run(studio):
    client, sessions, ids, path = api_conversation(studio)
    first = post_ok(
        client,
        path + "/messages",
        {
            "request_key": str(uuid.uuid4()),
            "content": "制作短片",
            "mode": "test",
        },
    )
    rid = first["messages"][-1]["task_id"]
    second = post_ok(
        client,
        path + "/messages",
        {
            "request_key": str(uuid.uuid4()),
            "content": "另外生成参考图",
            "media": {"provider": "mock", "request_type": "image"},
        },
    )
    assert second["messages"][-1]["task_id"] != rid
    unclear = {"request_key": str(uuid.uuid4()), "content": "这个稍微调整一下", "mode": "test"}
    assert client.post(path + "/messages", json=unclear).status_code == 409
    with sessions() as db:
        run = db.get(ProductionRun, uuid.UUID(rid))
        target = {"run_id": rid, "action": "guidance", "expected_revision": run.revision}
    result = post_ok(client, path + "/messages", {**unclear, "production": target})
    assert result["messages"][-1]["task_id"] == rid
    assert result["messages"][-1]["context"]["production"]["guidance"]["status"] == "assessing"
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(ProductionRun)) == 2


def test_http_global_guidance_confirmation_is_explicit_and_same_conversation(studio):
    client, sessions, ids, path = api_conversation(studio)
    first = post_ok(
        client,
        path + "/messages",
        {
            "request_key": str(uuid.uuid4()),
            "content": "制作短片",
            "mode": "test",
        },
    )
    rid = uuid.UUID(first["messages"][-1]["task_id"])
    post_ok(
        client,
        path + "/messages",
        {
            "request_key": str(uuid.uuid4()),
            "content": "整个剧本推倒重来",
            "mode": "test",
        },
    )
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        ticket = service.claim(db, rid)
        target = next(t for t in service.tasks(db, run) if not guidance.is_impact(t))
        gid = guidance.pending(run)
        guidance.resolve(
            db,
            ticket,
            guidance.Decision(
                guidance_id=gid,
                action="global",
                message="需要重新准备整段剧本，确认后继续。",
                evidence=[{"task_id": str(target.id), "quote": "整个剧本推倒重来"}],
            ),
        )
        production.publish(db, run)
        rev = run.revision
        db.commit()
    waiting = client.get(path).json()["messages"][-1]
    assert waiting["status"] == "needs_review"
    assert waiting["context"]["production"]["guidance"]["confirmation"]["expected_revision"] == rev
    payload = {
        "request_key": str(uuid.uuid4()),
        "content": "确认整体调整",
        "mode": "test",
        "production": {
            "action": "confirm_guidance",
            "run_id": str(rid),
            "guidance_id": gid,
            "expected_revision": rev,
        },
    }
    approved = post_ok(client, path + "/messages", payload)
    assert approved["messages"][-1]["task_id"] == str(rid)
    assert approved["messages"][-1]["context"]["production"]["guidance"]["status"] == "applied"
    assert len(post_ok(client, path + "/messages", payload)["messages"]) == 6


def test_http_direct_media_generates_real_local_output_without_text_model(studio):
    client, sessions, ids, path = api_conversation(studio)
    payload = {
        "request_key": str(uuid.uuid4()),
        "content": "一只蓝色杯子",
        "mode": "standard",
        "model_provider": "there-is-no-text-model",
        "media": {"provider": "mock", "request_type": "image"},
    }
    created = post_ok(client, path + "/messages", payload)
    run_id = uuid.UUID(created["messages"][-1]["task_id"])
    with sessions() as db:
        ticket = service.claim(db, run_id)
        db.commit()
    runtime.execute(ticket, sessions)
    with sessions() as db:
        # Scheduler observes the same saved job, then claims technical validation.
        validation = service.claim(db, run_id)
        assert validation
        task = db.get(ProductionTask, uuid.UUID(validation["task_id"]))
        assert task.state["validation_job_id"]
        db.commit()
    runtime.execute(validation, sessions)
    detail = client.get(path).json()
    reply = detail["messages"][-1]
    assert reply["status"] == "completed", reply
    assert len(reply["context"]["outputs"]) == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        run = db.get(ProductionRun, run_id)
        assert len(run.context["budget_reservations"]) == 1
        assert run.context["direct_request"]
    assert len(post_ok(client, path + "/messages", payload)["messages"]) == 2
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
