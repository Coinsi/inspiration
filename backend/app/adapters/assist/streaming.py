"""Bounded SSE decoding and request-local progress; never retries a submission."""

import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from io import StringIO

_progress = ContextVar("model_progress", default=None)
MAX_REPLY_BYTES = 2 * 1024 * 1024
# Providers can send an envelope for every token. Bound transport separately from reply text.
MAX_TRANSPORT_BYTES = 64 * 1024 * 1024


class CompletionReadError(ValueError):
    """Only locally authored messages from this reader are safe to show to users."""


def _check_transport(size):
    if size > MAX_TRANSPORT_BYTES:
        raise CompletionReadError("模型回复的传输数据超过平台接收上限")


def _check_finish(reason):
    if not reason or reason == "stop":
        return
    if reason == "length":
        raise CompletionReadError("模型输出达到长度上限，回复未完整结束")
    if reason == "content_filter":
        raise CompletionReadError("上游模型因内容审核停止了回复")
    raise CompletionReadError("模型回复未完整结束")


class _Reply:
    def __init__(self):
        self.text = StringIO()
        self.size = 0
        self.usage = None
        self.finished = False

    def append(self, delta):
        self.size += len(delta.encode("utf-8"))
        if self.size > MAX_REPLY_BYTES:
            raise CompletionReadError("模型回复正文超过平台的 2 MiB 接收上限")
        self.text.write(delta)

    def consume(self, data):
        if data.get("error"):
            raise CompletionReadError("上游模型在返回回复时报告错误")
        self.usage = data.get("usage") or self.usage
        for choice in data.get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            reason = choice.get("finish_reason")
            _check_finish(reason)
            if reason == "stop":
                self.finished = True
            delta = (choice.get("delta") or {}).get("content")
            if isinstance(delta, str) and delta:
                self.append(delta)
                callback = _progress.get()
                if callback:
                    callback(self.text.getvalue())

    def result(self):
        if not self.finished:
            raise CompletionReadError("模型回复中断，未收到上游结束标记")
        if not self.size:
            raise CompletionReadError("上游模型返回了空回复")
        return self.text.getvalue(), self.usage


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
    reply = _Reply()
    if "text/event-stream" not in content_type:
        body = response.read(MAX_TRANSPORT_BYTES + 1)
        _check_transport(len(body))
        data = json.loads(body)
        choice = data["choices"][0]
        _check_finish(choice.get("finish_reason"))
        content = choice["message"]["content"]
        if isinstance(content, str):
            reply.append(content)
        return content, data.get("usage")
    event = []
    wire_bytes = 0

    def consume():
        if not event:
            return False
        raw = "\n".join(event)
        event.clear()
        if raw == "[DONE]":
            return True
        reply.consume(json.loads(raw))
        return False

    while True:
        if time.monotonic() >= deadline:
            raise CompletionReadError("接收模型回复超时，已超过 4 分钟接收时限")
        line = response.readline(MAX_TRANSPORT_BYTES - wire_bytes + 1)
        if not line:
            consume()
            break
        wire_bytes += len(line)
        _check_transport(wire_bytes)
        line = line.decode("utf-8").rstrip("\r\n")
        if not line:
            if consume():
                break
        elif line.startswith("data:"):
            event.append(line[5:].lstrip())
    return reply.result()


def read_sdk_completion(response):
    """Consume normalized SDK chunks while preserving strict completion/progress semantics."""
    deadline = time.monotonic() + 240
    reply = _Reply()
    envelope_bytes = 0
    try:
        for chunk in response:
            if time.monotonic() >= deadline:
                raise CompletionReadError("接收模型回复超时，已超过 4 分钟接收时限")
            data = chunk.model_dump() if hasattr(chunk, "model_dump") else chunk
            envelope_bytes += len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            _check_transport(envelope_bytes)
            reply.consume(data)
        # The pinned SDK synthesizes a stop chunk on EOF. It must not authorize
        # an Agent action unless the provider actually supplied a finish reason.
        if hasattr(response, "received_finish_reason") and not (
            response.received_finish_reason or response.intermittent_finish_reason
        ):
            raise CompletionReadError("模型回复中断，未收到上游结束标记")
        # The SDK can estimate tokens when the provider omits usage. Do not
        # record an estimate as confirmed upstream usage in the resource ledger.
        if hasattr(response, "chunks") and not any(
            (c.get("usage") if isinstance(c, dict) else getattr(c, "usage", None))
            for c in response.chunks
        ):
            reply.usage = None
        return reply.result()
    finally:
        # CustomStreamWrapper in the pinned SDK only exposes asynchronous close;
        # our synchronous callers must close its underlying HTTP stream directly.
        source = getattr(response, "completion_stream", None)
        close = getattr(source, "close", None) or getattr(response, "close", None)
        if close:
            close()
