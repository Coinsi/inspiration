"""The same typed engineering tools run without an AgentRun or model provider."""

# ruff: noqa: F811
import json
import uuid
from types import SimpleNamespace

import pytest

from app.core.errors import Forbidden
from app.models.enums import Role
from app.modules.tools import registry
from app.modules.tools.contracts import ToolContext
from tests.test_engineering_review import ready
from tests.test_media_workflow import studio  # noqa: F401


def test_direct_tool_candidate_shares_api_state_and_scope(studio):
    client, sessions, ids, path, _, _ = ready(studio)
    wid = uuid.UUID(path.split("/")[-2])
    ctx = SimpleNamespace(
        project=SimpleNamespace(id=uuid.UUID(ids["project"])),
        user=SimpleNamespace(id=ids["user"]),
        role=Role.admin,
    )
    args = {
        "work_id": str(wid),
        "request_key": str(uuid.uuid4()),
        "base_revision": 0,
        "commands": [
            {
                "operation": "document.put",
                "document_id": str(uuid.uuid4()),
                "expected_revision": 0,
                "kind": "image.document",
                "title": "工具画布",
                "payload": {"canvas": {"width": 16, "height": 16}, "layers": []},
            }
        ],
    }
    with sessions() as db:
        with pytest.raises(Forbidden):
            registry.execute(db, ctx, ToolContext(), "engineering.propose", args)
        result = registry.execute(db, ctx, ToolContext(work_id=wid), "engineering.propose", args)
        json.dumps(result)  # Persistable by any agent runtime, no ORM objects.
        db.commit()
    assert client.get(path).json()["revision"] == 0
    assert (
        client.get(path + f"/changes/{result['id']}").json()["fingerprint"] == result["fingerprint"]
    )
    with sessions() as db:
        read = registry.execute(
            db,
            ctx,
            ToolContext(),
            "engineering.read",
            {"work_id": str(wid), "view": "change", "change_id": result["id"]},
        )
        assert read["impact"]["affected_outputs"]
    assert {t["name"] for t in registry.catalogue("engineering")} == {
        "engineering.read",
        "engineering.propose",
        "engineering.preview",
    }
    assert "engineering.accept" not in registry.CATALOG


def test_registered_extension_dispatches_without_core_branch(monkeypatch):
    monkeypatch.setattr(registry, "CATALOG", dict(registry.CATALOG))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    registry.register(
        registry.Tool("test.extension", "test", registry.Empty, group="test"),
        lambda db, ctx, context, args: {"ok": True},
    )
    assert registry.execute(None, None, ToolContext(), "test.extension", {}) == {"ok": True}
    with pytest.raises(ValueError):
        registry.register(registry.Tool("test.extension", "test", registry.Empty), lambda *args: {})
