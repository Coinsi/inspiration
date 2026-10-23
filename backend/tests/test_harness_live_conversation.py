"""Actual DB projections/auth for partial replies; no external model calls."""

# ruff: noqa: F811
import json
import uuid

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.core.errors import AppError
from app.models.harness import ProductionRun
from app.modules.conversation import live, production
from app.modules.harness import runtime, schemas, service
from app.modules.harness.live_progress import writer
from tests.test_harness_conversation import conversation, start
from tests.test_media_workflow import studio  # noqa: F401


def test_live_partial_reply_is_fenced_and_failed_output_is_cleared(studio, monkeypatch):
    client, sessions, ids = studio
    monkeypatch.setattr(live, "SessionLocal", sessions)
    with sessions() as db:
        c = conversation(db, ids)
        message, run, _ = start(db, ids, c)
        cid, rid = c.id, run.id
        db.commit()
        ticket = service.claim(db, rid)
        db.commit()
    writer(sessions, ticket)('{"kind":"finish","message":"正在回复')
    credential = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=client.headers["Authorization"].split(" ", 1)[1]
    )
    data = json.loads(live.snapshot(credential, uuid.UUID(ids["project"]), cid))
    assert data["messages"][-1]["content"] == "正在回复"
    assert data["messages"][-1]["status"] == "running"
    with sessions() as db:
        service.finish_attempt(db, ticket, status="failed", error={"message": "断流"})
        production.publish(db, db.get(ProductionRun, rid))
        db.commit()
    writer(sessions, ticket)('{"kind":"finish","message":"旧请求不能覆盖')
    data = json.loads(live.snapshot(credential, uuid.UUID(ids["project"]), cid))
    assert data["messages"][-1]["status"] == "failed"
    assert data["messages"][-1]["content"] == ""
    with pytest.raises(AppError):
        live.snapshot(credential, ids["other"], cid)
    with pytest.raises(AppError):
        live.snapshot(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad"),
            uuid.UUID(ids["project"]),
            cid,
        )


def test_unfiltered_discovery_is_index_and_loaded_window_is_bounded(studio):
    _, sessions, ids = studio
    with sessions() as db:
        c = conversation(db, ids)
        _, run, _ = start(db, ids, c)
        rid = run.id
        db.commit()

    def discover(arguments):
        with sessions() as db:
            ticket = service.claim(db, rid)
            db.commit()
            runtime.apply_action(
                db,
                ticket,
                schemas.ToolAction(kind="tool", tool="capabilities.discover", arguments=arguments),
            )
            db.commit()
            return service.tasks(db, db.get(ProductionRun, rid))[0].state

    state = discover({})
    assert state["loaded_tools"] == []
    assert all("input_schema" not in t for t in state["steps"][-1]["receipt"]["data"])
    for name in [
        "project.overview",
        "media.models",
        "media.search",
        "media.inspect",
        "media.estimate",
    ]:
        state = discover({"names": [name]})
    assert len(state["loaded_tools"]) == 4 and "project.overview" not in state["loaded_tools"]


def test_ordinary_chat_initial_request_contains_no_business_tool_schemas(studio):
    _, sessions, ids = studio
    with sessions() as db:
        c = conversation(db, ids)
        _, run, _ = start(db, ids, c, content="你好")
        db.commit()
        ticket = service.claim(db, run.id)
        db.commit()
        packet = runtime.prepare(db, ticket)["packet"]
    assert [tool["name"] for tool in packet["tools"]] == ["capabilities.discover"]
    wire = json.dumps(packet, ensure_ascii=False).encode("utf-8")
    assert len(wire) < 16000
