"""Authenticated SSE snapshots backed by durable messages, across worker processes."""

import asyncio
import json
import time

from fastapi.encoders import jsonable_encoder

from app.core.database import SessionLocal
from app.core.deps import get_current_user, get_project_context
from app.modules.conversation import service


def snapshot(credentials, project_id, conversation_id):
    with SessionLocal() as db:
        user = get_current_user(cred=credentials, db=db)
        ctx = get_project_context(project_id=project_id, user=user, db=db)
        value = service.detail(db, ctx, conversation_id)
        db.commit()
        return json.dumps(jsonable_encoder(value), ensure_ascii=False, separators=(",", ":"))


async def events(request, credentials, project_id, conversation_id):
    previous, heartbeat = None, time.monotonic()
    # Reconnect periodically to re-establish proxy/auth state. No DB session held while waiting.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if await request.is_disconnected():
            break
        try:
            current = await asyncio.to_thread(snapshot, credentials, project_id, conversation_id)
        except Exception:
            yield "event: unavailable\ndata: {}\n\n"
            return
        if current != previous:
            yield f"event: snapshot\ndata: {current}\n\n"
            previous = current
        elif time.monotonic() - heartbeat > 10:
            yield ": heartbeat\n\n"
            heartbeat = time.monotonic()
        await asyncio.sleep(0.5)
