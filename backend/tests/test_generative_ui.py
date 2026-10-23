"""Generated interfaces are validated, durable, scoped data with no write capability."""

import json
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.modules.agent import worker
from app.modules.agent.schemas import ModelAction
from app.modules.tools.generative_ui import COMPONENTS, PresentArgs
from tests.test_conversations import message, setup
from tests.test_media_workflow import post_ok, studio  # noqa: F401

FORM = {
    "_type": "Card",
    "title": "人物设定",
    "asForm": True,
    "confirm": {"label": "确认人物", "_action": {"type": "reply", "text": "采用这个人物"}},
    "children": [{"_type": "Input", "name": "name", "label": "姓名", "defaultValue": "林舟"}],
}


def test_complete_official_vocabulary_and_invalid_payloads():
    assert len(COMPONENTS) == 35
    assert PresentArgs.model_validate(FORM).root == FORM
    for invalid in [
        {"_type": "Script", "code": "alert(1)"},
        {"_type": "Image", "src": "javascript:alert(1)", "alt": "bad"},
        {"_type": "Button", "label": "保存", "_action": {"type": "delete"}},
        {"_type": "Card", "background": "url(https://example.com/track)"},
    ]:
        with pytest.raises(ValueError):
            PresentArgs.model_validate(invalid)


def test_all_35_component_fixtures_validate_against_shipped_schema():
    fixtures = json.loads(
        (Path(__file__).parents[2] / "frontend/src/dev/generative-ui-fixtures.json").read_text(
            "utf-8"
        )
    )
    assert {item["_type"] for item in fixtures} == COMPONENTS
    for item in fixtures:
        PresentArgs.model_validate(item)


def test_present_survives_reload_and_enters_followup_context(studio, monkeypatch):  # noqa: F811
    # This regression drives the legacy worker explicitly; the default route is now Harness.
    monkeypatch.setattr(settings, "production_harness_enabled", False)
    client, sessions, ids, base, path = setup(studio, monkeypatch)
    sent = post_ok(client, path + "/messages", message("用表单展示人物"))
    run_id = uuid.UUID(sent["messages"][-1]["task_id"])
    monkeypatch.setattr(worker, "propose", lambda c, p: ModelAction(tool="present", arguments=FORM))
    worker.advance(run_id)
    worker.advance(run_id)
    ui = client.get(path).json()["messages"][-1]["context"]["ui_blocks"]
    assert ui[0]["tree"] == FORM
    monkeypatch.setattr(
        worker, "propose", lambda c, p: ModelAction(tool="finish", message="请确认人物")
    )
    worker.advance(run_id)
    assert client.get(path).json()["messages"][-1]["context"]["ui_blocks"] == ui
    assert not client.get(path).json()["messages"][0]["context"].get("ui_blocks")
    next_turn = post_ok(client, path + "/messages", message('采用这个人物：{"name":"林舟"}'))
    seen = []

    def model(c, payload):
        seen.append(payload)
        return ModelAction(tool="finish", message="已收到选择，尚未保存到项目")

    monkeypatch.setattr(worker, "propose", model)
    worker.advance(uuid.UUID(next_turn["messages"][-1]["task_id"]))
    assert seen[0]["conversation_context"]["messages"][-1]["ui_blocks"] == ui
