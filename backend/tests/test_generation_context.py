"""Contextual generation contract; real isolated DB/worker, stubbed text model."""

# ruff: noqa: F811
import copy
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.adapters.assist import cloud_llm
from app.core.errors import Conflict, ProviderError
from app.models.conversation import ConversationMessage
from app.models.generation import Generation, GenerationJob
from app.models.harness import ProductionRun
from app.modules.conversation import production
from app.modules.conversation.service import history_snapshot
from app.modules.generation import media_engine
from app.modules.harness import generation_context as gc
from app.modules.harness import kernel_modalities, runtime, schemas, service
from app.modules.harness.media_tools import GenerateArgs
from app.storage import cas
from tests.test_harness_context import context
from tests.test_harness_conversation import conversation, start
from tests.test_media_workflow import studio  # noqa: F401


@pytest.fixture
def compiler(monkeypatch):
    calls = []
    response = {
        "prompt": "黑衣女主在白天的山谷，水彩风格，无文字。",
        "summary": "沿用女主与水彩风格，按最新要求改为白天。",
        "reference_keys": [],
        "question": "",
    }

    def chat(system, parts, *_, **kwargs):
        calls.append({"system": system, "packet": json.loads(parts[0]["text"]), "media": parts[1:]})
        return json.dumps(response, ensure_ascii=False), {"total_tokens": 100}

    monkeypatch.setattr(cloud_llm, "_chat", chat)
    monkeypatch.setattr(
        kernel_modalities,
        "resolve_cloud_input",
        lambda *a, **kw: {
            "config": {
                "base_url": "https://fixture.invalid/v1",
                "api_key": None,
                "model": "fixture",
            },
            "input_contract": kernel_modalities.InputCapabilities().model_dump(),
            "profile": {"kind": "environment"},
        },
    )
    return calls, response


def create_run(sessions, ids, history, content="根据上面讨论的生成一张图", request_type="image"):
    with sessions() as db:
        c = conversation(db, ids)
        message, run, _ = start(
            db,
            ids,
            c,
            mode="standard",
            content=content,
            history={"messages": history},
            media={"provider": "mock", "request_type": request_type},
        )
        db.commit()
        ticket = service.claim(db, run.id)
        production.publish(db, run)
        assert message.context["media_progress"]["phase"] == "queued"
        assert "整理" in message.context["media_progress"]["title"]
        db.commit()
        return run.id, message.id, ticket


@pytest.mark.parametrize("request_type", ["image", "video"])
def test_worker_keeps_full_retained_history_and_freezes_one_generation(
    studio, compiler, request_type
):
    _, sessions, ids = studio
    calls, response = compiler
    history = [{"id": str(uuid.uuid4()), "role": "user", "content": "黑衣女主，水彩风格，夜晚山谷"}]
    history += [{"role": "assistant", "content": "镜头构思"} for _ in range(10)]
    history += [{"role": "user", "content": "不要夜晚，改成白天，画面无文字"}]
    rid, mid, ticket = create_run(sessions, ids, history, request_type=request_type)
    runtime.execute(ticket, sessions)
    assert calls[0]["packet"]["context"]["history"] == history
    assert "tools" not in calls[0]["packet"]
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        task = service.tasks(db, run)[0]
        assert task.status == "waiting_job", task.error
        brief = task.state["generation_brief"]
        assert brief["prompt"] == response["prompt"]
        assert brief["arguments"]["count"] == 1
        assert brief["arguments"]["provider"] == "mock"
        assert brief["arguments"]["request_type"] == request_type
        assert brief["source_message_ids"] == [history[0]["id"]]
        job = db.scalar(select(GenerationJob))
        assert response["prompt"] in job.input_snapshot["prompt"]
        public = db.get(ConversationMessage, mid).context["generation_brief"]
        assert "arguments" not in public and public["prompt"] == response["prompt"]
        # A worker crash/lease replay must not compile or submit again.
        task.status = "queued"
        task.state = {
            k: v for k, v in task.state.items() if k not in {"waiting_job_id", "validation_job_id"}
        }
        run.status = "queued"
        db.flush()
        second = service.claim(db, rid)
        db.commit()
    response["prompt"] = "不应被使用的新要求"
    runtime.execute(second, sessions)
    assert len(calls) == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert service.tasks(db, db.get(ProductionRun, rid))[0].state["generation_brief"] == brief


def test_ambiguity_asks_in_chat_and_reply_resumes_before_dispatch(studio, compiler):
    _, sessions, ids = studio
    calls, response = compiler
    response.update(prompt="", summary="", question="采用山谷还是城市方案？")
    rid, _, ticket = create_run(
        sessions, ids, [{"role": "assistant", "content": "方案一山谷，方案二城市"}]
    )
    runtime.execute(ticket, sessions)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        task = service.tasks(db, run)[0]
        assert task.status == "waiting_input", task.error
        message = db.scalar(
            select(ConversationMessage).where(ConversationMessage.execution_id == rid)
        )
        assert message.context["media_progress"] is None
        assert message.content == "采用山谷还是城市方案？"
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0
        service.control(
            db,
            context(db, ids),
            rid,
            schemas.Control(
                request_key=uuid.uuid4(),
                expected_revision=run.revision,
                action="reply",
                task_ids=[task.id],
                message="山谷，保留水彩",
            ),
        )
        db.commit()
        second = service.claim(db, rid)
        db.commit()
    response.update(prompt="白天山谷，水彩风格", question="")
    runtime.execute(second, sessions)
    assert calls[-1]["packet"]["answer"] == "山谷，保留水彩"
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1


def test_model_output_cannot_inject_routes_or_unknown_references(compiler):
    _, response = compiler
    value = {"policy": {"max_model_output_tokens": 1000}, "reference_candidates": []}
    config = {"base_url": "https://fixture.invalid", "api_key": None, "model": "fixture"}
    base = GenerateArgs(provider="mock", request_type="image", prompt="原要求").model_dump(
        mode="json"
    )
    response["reference_keys"] = ["generation:" + str(uuid.uuid4())]
    with pytest.raises(ProviderError):
        gc.decide(config, value, base)
    response["reference_keys"] = []
    response["provider"] = "other"
    with pytest.raises(ProviderError):
        gc.decide(config, value, base)


def test_freeze_rejects_parameter_changes_and_preserves_explicit_roles(monkeypatch):
    ident = str(uuid.uuid4())
    base = GenerateArgs(
        provider="mock",
        request_type="video",
        prompt="原要求",
        references=[{"generation_id": ident, "role": "first_frame"}],
    ).model_dump(mode="json")
    ref = {"kind": "generation", "id": ident, "expected_blob_hash": "abc"}
    run = SimpleNamespace(context={"references": [ref], "history": []})
    task = SimpleNamespace(specification={"arguments": base}, state={})
    from app.modules.harness import context as context_module

    monkeypatch.setattr(context_module, "resolve_reference", lambda *a: {"reference": ref})
    args = {**base, "prompt": "完善的要求"}
    action = schemas.ToolAction(kind="tool", tool="media.generate", arguments=args)
    saved = gc.freeze(None, None, run, task, action)
    assert saved["arguments"]["references"][0]["role"] == "first_frame"
    for change in ({"provider": "another"}, {"count": 2}, {"references": []}):
        with pytest.raises(Conflict):
            gc.freeze(
                None, None, run, task, action.model_copy(update={"arguments": {**args, **change}})
            )
    task.state = {"generation_brief": copy.deepcopy(saved)}
    with pytest.raises(Conflict):
        gc.freeze(
            None,
            None,
            run,
            task,
            action.model_copy(update={"arguments": {**args, "prompt": "changed"}}),
        )


def test_media_only_messages_survive_history_snapshot():
    output = {"id": str(uuid.uuid4()), "type": "image", "blob_hash": "abc"}
    message = SimpleNamespace(
        id=uuid.uuid4(),
        ordinal=2,
        role="assistant",
        content="",
        error=None,
        status="completed",
        context={
            "outputs": [output],
            "generation_brief": {"prompt": "水彩山谷", "summary": "沿用设定", "references": []},
        },
    )
    history = history_snapshot([message])
    assert history["messages"][0]["media_outputs"] == [output]
    assert history["messages"][0]["generation_brief"]["prompt"] == "水彩山谷"


def test_preparation_failure_is_retryable_without_generation(studio, compiler, monkeypatch):
    _, sessions, ids = studio
    calls, _ = compiler
    rid, _, ticket = create_run(sessions, ids, [{"role": "user", "content": "水彩山谷"}])
    chat = cloud_llm._chat

    def unavailable(*args, **kwargs):
        raise ProviderError("provider response must not be published")

    monkeypatch.setattr(cloud_llm, "_chat", unavailable)
    runtime.execute(ticket, sessions)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        task = service.tasks(db, run)[0]
        assert task.status == "failed" and task.error["retryable"] is True
        assert "尚未提交生成" in task.error["message"]
        assert "provider response" not in task.error["message"]
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0
        service.control(
            db,
            context(db, ids),
            rid,
            schemas.Control(
                request_key=uuid.uuid4(),
                expected_revision=run.revision,
                action="retry",
                task_ids=[task.id],
            ),
        )
        db.commit()
        second = service.claim(db, rid)
        db.commit()
    monkeypatch.setattr(cloud_llm, "_chat", chat)
    runtime.execute(second, sessions)
    assert len(calls) == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1


def test_missing_history_with_explicit_backward_reference_is_not_sent_directly(studio, compiler):
    _, sessions, ids = studio
    calls, response = compiler
    response.update(
        prompt="", summary="", question="这段对话中还没有角色设定，请补充想沿用的人物。"
    )
    rid, _, ticket = create_run(sessions, ids, [])
    runtime.execute(ticket, sessions)
    assert calls[0]["packet"]["context"]["history"] == []
    with sessions() as db:
        task = service.tasks(db, db.get(ProductionRun, rid))[0]
        assert task.status == "waiting_input"
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0


@pytest.mark.parametrize("reference_support", [True, False])
def test_historical_image_is_pinned_observed_and_forwarded_without_index(
    studio, compiler, monkeypatch, reference_support
):
    _, sessions, ids = studio
    calls, response = compiler
    if reference_support:
        from app.adapters.generation.mock import MockProvider

        capabilities = MockProvider.capabilities
        monkeypatch.setattr(
            MockProvider,
            "capabilities",
            lambda self: capabilities(self).model_copy(
                update={"features": {"reference"}, "max_reference_images": 4}
            ),
        )
    with sessions() as db:
        blob = cas.put_bytes(db, media_engine.mock_image(), "image/png", width=512, height=512)
        old_job = GenerationJob(
            project_id=uuid.UUID(ids["project"]),
            target_type="shot",
            target_id=uuid.UUID(ids["shot"]),
            provider="mock",
            request_type="image",
            status="succeeded",
            input_snapshot={},
            created_by=ids["user"],
        )
        db.add(old_job)
        db.flush()
        image = Generation(
            project_id=uuid.UUID(ids["project"]),
            job_id=old_job.id,
            target_type="shot",
            target_id=uuid.UUID(ids["shot"]),
            provider="mock",
            output_type="image",
            output_blob_hash=blob.hash,
        )
        db.add(image)
        db.commit()
        image_id, blob_hash = str(image.id), blob.hash
    monkeypatch.setattr(
        kernel_modalities,
        "resolve_cloud_input",
        lambda *a, **kw: {
            "config": {"base_url": "https://fixture.invalid", "api_key": None, "model": "fixture"},
            "input_contract": kernel_modalities.InputCapabilities(
                modalities=["text", "image"], max_images=4
            ).model_dump(),
            "profile": {"kind": "environment"},
        },
    )
    response["reference_keys"] = ["generation:" + image_id]
    history = [
        {
            "role": "assistant",
            "content": "女主设定图",
            "media_outputs": [{"id": image_id, "type": "image", "blob_hash": blob_hash}],
        }
    ]
    rid, _, ticket = create_run(
        sessions, ids, history, content="沿用上一张的人物，把场景换成白天山谷"
    )
    runtime.execute(ticket, sessions)
    assert any(p["type"] == "image_url" for p in calls[0]["media"])
    candidate = calls[0]["packet"]["reference_candidates"][0]
    assert candidate["reference"]["expected_blob_hash"] == blob_hash
    with sessions() as db:
        task = service.tasks(db, db.get(ProductionRun, rid))[0]
        if not reference_support:
            assert task.status == "failed"
            assert task.error["code"] == "CAPABILITY_UNSUPPORTED"
            assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
            return
        assert task.status == "waiting_job", task.error
        args = task.state["generation_brief"]["arguments"]
        assert args["references"][0]["generation_id"] == image_id
        assert task.state["generation_brief"]["references"][0]["expected_blob_hash"] == blob_hash
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 2


def test_pause_during_compilation_resumes_validated_action_without_second_model_call(
    studio, compiler, monkeypatch
):
    _, sessions, ids = studio
    calls, _ = compiler
    rid, _, ticket = create_run(sessions, ids, [{"role": "user", "content": "主角在水彩山谷"}])
    chat = cloud_llm._chat

    def pause(*args, **kwargs):
        result = chat(*args, **kwargs)
        with sessions() as db:
            run = db.get(ProductionRun, rid)
            service.control(
                db,
                context(db, ids),
                rid,
                schemas.Control(
                    request_key=uuid.uuid4(), expected_revision=run.revision, action="pause"
                ),
            )
            db.commit()
        return result

    monkeypatch.setattr(cloud_llm, "_chat", pause)
    runtime.execute(ticket, sessions)
    with sessions() as db:
        run = db.get(ProductionRun, rid)
        assert run.status == "paused"
        task = service.tasks(db, run)[0]
        assert task.state["pending_action"]["tool"] == "media.generate"
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0
        service.control(
            db,
            context(db, ids),
            rid,
            schemas.Control(
                request_key=uuid.uuid4(), expected_revision=run.revision, action="resume"
            ),
        )
        db.commit()
        second = service.claim(db, rid)
        db.commit()
    runtime.execute(second, sessions)
    assert len(calls) == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
