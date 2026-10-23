"""Persist only user-facing finish text, fenced by the active attempt lease."""

import json
import re
import time

PREFIX = re.compile(r'^\s*\{\s*"kind"\s*:\s*"finish"\s*,\s*"message"\s*:\s*"((?:[^"\\]|\\.)*)')


def preview(raw):
    match = PREFIX.match(raw)
    if not match:
        return ""
    text = match.group(1)
    # A split escape/unicode sequence is not displayable until the next delta.
    for count in range(min(6, len(text)) + 1):
        try:
            return json.loads('"' + (text[:-count] if count else text) + '"')[:16000]
        except ValueError:
            pass
    return ""


def writer(sessions, ticket):
    last = 0.0

    def update(raw):
        nonlocal last
        now = time.monotonic()
        if now - last < 0.4:
            return
        text = preview(raw)
        if not text:
            return
        last = now
        from app.modules.conversation.production import publish
        from app.modules.harness.runtime import _get_locked

        with sessions() as db:
            locked = _get_locked(db, ticket)
            if not locked:
                return
            run, task, _ = locked
            if run.status != "running":
                return
            task.state = {**task.state, "model_preview": text}
            db.flush()
            publish(db, run)
            db.commit()

    return update
