"""Bounded SSE decoding and request-local progress; never retries a submission."""

import json
import time
from contextlib import contextmanager
from contextvars import ContextVar

_progress = ContextVar("model_progress", default=None)


@contextmanager
def progress(callback):
    token = _progress.set(callback)
    try:
        yield
    finally:
        _progress.reset(token)


def read_completion(response):
    deadline = time.monotonic() + 240
    content_type = response.headers.get("Content-Type", "")
    if "text/event-stream" not in content_type:
        body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError("模型响应超过接收限制")
        data = json.loads(body)
        choice = data["choices"][0]
        if choice.get("finish_reason") not in {None, "stop"}:
            raise ValueError("模型回复未完整结束")
        return choice["message"]["content"], data.get("usage")
    chunks, event, usage = [], [], None
    total, finished = 0, False

    def consume():
        nonlocal usage, finished
        if not event:
            return False
        raw = "\n".join(event)
        event.clear()
        if raw == "[DONE]":
            return True
        data = json.loads(raw)
        if data.get("error"):
            raise ValueError("模型流返回错误")
        usage = data.get("usage") or usage
        for choice in data.get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            reason = choice.get("finish_reason")
            if reason:
                if reason != "stop":
                    raise ValueError("模型回复未完整结束")
                finished = True
            delta = choice.get("delta", {}).get("content")
            if isinstance(delta, str):
                chunks.append(delta)
                callback = _progress.get()
                if callback:
                    callback("".join(chunks))
        return False

    while True:
        if time.monotonic() >= deadline:
            raise ValueError("模型流超过接收时限")
        line = response.readline(65537)
        if not line:
            consume()
            break
        total += len(line)
        if len(line) > 65536 or total > 2 * 1024 * 1024:
            raise ValueError("模型响应超过接收限制")
        line = line.decode("utf-8").rstrip("\r\n")
        if not line:
            if consume():
                break
        elif line.startswith("data:"):
            event.append(line[5:].lstrip())
    if not finished or not chunks:
        raise ValueError("模型流中断，未收到完整回复")
    return "".join(chunks), usage
