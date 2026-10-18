from contextlib import contextmanager

import pytest

from app.core.errors import CapabilityUnsupported, Conflict
from app.modules.engineering import documents, executors


def test_frozen_executor_is_not_replaced_by_new_default(monkeypatch):
    monkeypatch.setattr(executors, "_EXECUTORS", dict(executors._EXECUTORS))
    monkeypatch.setattr(executors, "_DEFAULTS", dict(executors._DEFAULTS))

    @contextmanager
    def first(*args):
        yield b"first"

    @contextmanager
    def second(*args):
        yield b"second"

    old = executors.Executor("test.executor@1", "test.document", 1, "image", None, first, (), 1024)
    new = executors.Executor("test.executor@2", "test.document", 1, "image", None, second, (), 1024)
    executors.register(old)
    body = {"executor": old.id, "root": {}, "nodes": []}
    plan = {**body, "fingerprint": documents.digest({}, body)}
    executors.register(new, default=True)
    with executors.execute(plan, None, None, lambda: False) as (result, typ):
        assert (result, typ) == (b"first", "image")
    with pytest.raises(ValueError):
        executors.register(old)
    with pytest.raises(Conflict):
        executors.verify({**plan, "executor": new.id})
    with pytest.raises(CapabilityUnsupported):
        executors.verify({**plan, "executor": "missing"})
