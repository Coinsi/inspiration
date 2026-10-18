"""A separately registered business document exercises the public engineering API."""

# ruff: noqa: F811
import uuid

import pytest

from app.core.errors import CapabilityUnsupported
from app.modules.engineering import document_types as registry
from tests.test_engineering import accept
from tests.test_engineering_review import ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


@pytest.fixture
def extension():
    kind = "test.title-card"

    def first(inputs, payload):
        if inputs or set(payload) != {"text"} or not isinstance(payload["text"], str):
            raise ValueError("invalid title card version 1")

    def second(inputs, payload):
        if inputs or set(payload) != {"text", "align"} or payload["align"] != "center":
            raise ValueError("invalid title card version 2")

    registry.register(registry.DocumentType(kind, 1, first, False, True, ("picture",)))
    try:
        yield kind, second
    finally:
        for key in list(registry._TYPES):
            if key[0] == kind:
                registry._TYPES.pop(key)
        for key in list(registry._MIGRATIONS):
            if key[0] == kind:
                registry._MIGRATIONS.pop(key)
        registry.CURRENT.pop(kind, None)


def test_extension_schema_migration_keeps_old_revisions_readable(studio, extension):
    kind, second = extension
    client, _, _, path, _, _ = ready(studio)
    identity = str(uuid.uuid4())
    first = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 0,
            "commands": [
                {
                    "operation": "document.put",
                    "document_id": identity,
                    "expected_revision": 0,
                    "kind": kind,
                    "title": "字幕卡",
                    "payload": {"text": "片名"},
                }
            ],
        },
    )
    accept(client, path, first)
    original = client.get(f"{path}/documents/{identity}/revisions/1").json()
    registry.register(registry.DocumentType(kind, 2, second, False, True, ("picture",)))
    # New registration alone cannot reinterpret or migrate any saved revision.
    assert client.get(f"{path}/documents/{identity}/revisions/1").json() == original
    with pytest.raises(CapabilityUnsupported):
        registry.migrate(kind, 1, 2, {}, {"text": "片名"})
    registry.register_migration(
        kind, 1, 2, lambda links, payload: (links, {**payload, "align": "center"})
    )
    migration = post_ok(
        client,
        path + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 1,
            "commands": [
                {
                    "operation": "document.migrate",
                    "document_id": identity,
                    "document_revision": 1,
                    "expected_revision": 1,
                    "target_schema_version": 2,
                }
            ],
        },
    )
    assert client.get(path).json()["revision"] == 1
    assert migration["snapshot"]["migrations"][-1]["steps"] == [{"from": 1, "to": 2}]
    accept(client, path, migration)
    new_ref = migration["snapshot"]["native_roots"][identity]
    updated = client.get(f"{path}/documents/{identity}/revisions/{new_ref['revision']}").json()
    assert updated["schema_version"] == 2
    assert updated["payload"] == {"text": "片名", "align": "center"}
    assert client.get(f"{path}/documents/{identity}/revisions/1").json() == original
    assert client.get(path + "/package?revision=1").status_code == 200
    # Registration cannot silently change the meaning of an output type.
    with pytest.raises(ValueError):
        registry.register(registry.DocumentType(kind, 3, second, False, True, ("sound",)))


def test_noop_payload_across_schema_versions_is_not_revision_alias(studio, extension):
    kind, _ = extension
    client, _, _, path, _, _ = ready(studio)
    identity = str(uuid.uuid4())
    original_adapter = registry.adapter(kind, 1)
    registry.register(
        registry.DocumentType(kind, 2, original_adapter.validate, False, True, ("picture",))
    )
    for base, schema in ((0, 1), (1, 2)):
        change = post_ok(
            client,
            path + "/changes",
            {
                "request_key": str(uuid.uuid4()),
                "base_revision": base,
                "commands": [
                    {
                        "operation": "document.put",
                        "document_id": identity,
                        "expected_revision": base,
                        "kind": kind,
                        "schema_version": schema,
                        "title": "版本测试",
                        "payload": {"text": "same"},
                    }
                ],
            },
        )
        accept(client, path, change)
    doc = client.get(f"{path}/documents/{identity}/revisions/2").json()
    assert doc["schema_version"] == 2
