"""Observe real ASGI response events: success must never precede the transaction commit."""

import asyncio

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from app.core import database
from app.main import app


def test_database_dependencies_close_before_response():
    found = 0

    def walk(dependency):
        nonlocal found
        if dependency.call is database.get_db:
            found += 1
            assert dependency.scope == "function"
        for child in dependency.dependencies:
            walk(child)

    for route in app.routes:
        if hasattr(route, "dependant"):
            walk(route.dependant)
    assert found > 30


@pytest.mark.parametrize("fail", [False, True])
def test_commit_completes_before_headers_and_failure_is_not_success(monkeypatch, fail):
    events = []

    class FakeSession:
        def commit(self):
            events.append("commit")
            if fail:
                raise RuntimeError("commit failed")

        def rollback(self):
            events.append("rollback")

        def close(self):
            events.append("close")

    monkeypatch.setattr(database, "SessionLocal", FakeSession)
    api = FastAPI()

    @api.get("/")
    def endpoint(db: Session = Depends(database.get_db, scope="function")):
        events.append("write")
        return {"saved": True}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(event):
        if event["type"] == "http.response.start":
            events.append(event["status"])

    async def request():
        await api(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "method": "GET",
                "path": "/",
                "raw_path": b"/",
                "query_string": b"",
                "root_path": "",
                "headers": [],
                "scheme": "http",
                "server": ("localhost", 80),
                "client": ("localhost", 1),
            },
            receive,
            send,
        )

    if fail:
        with pytest.raises(RuntimeError, match="commit failed"):
            asyncio.run(request())
        assert events == ["write", "commit", "rollback", "close", 500]
    else:
        asyncio.run(request())
        assert events == ["write", "commit", "close", 200]
