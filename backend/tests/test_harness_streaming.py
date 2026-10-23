import io
import json

import pytest

from app.adapters.assist.streaming import progress, read_completion
from app.core.errors import CapabilityUnsupported
from app.modules.harness.live_progress import preview
from app.modules.harness.prompt_budget import bounded


def response(events):
    result = io.BytesIO(events.encode())
    result.headers = {"Content-Type": "text/event-stream; charset=utf-8"}
    return result


def event(value):
    return "data: " + json.dumps(value, ensure_ascii=False) + "\n\n"


def test_stream_delivers_deltas_and_usage_before_returning():
    seen = []
    stream = response(
        ": heartbeat\n\n"
        + event({"choices": [{"delta": {"content": "你"}}]})
        + event({"choices": [{"delta": {"content": "好"}, "finish_reason": "stop"}]})
        + event({"choices": [], "usage": {"total_tokens": 9}})
        + "data: [DONE]\n\n"
    )
    with progress(seen.append):
        text, usage = read_completion(stream)
    assert seen == ["你", "你好"]
    assert text == "你好" and usage["total_tokens"] == 9


@pytest.mark.parametrize(
    "ending",
    [
        "",
        "data: [DONE]\n\n",
        event({"error": {"message": "bad"}}),
        event({"choices": [{"finish_reason": "length"}]}),
    ],
)
def test_incomplete_or_failed_stream_is_never_a_completed_action(ending):
    with pytest.raises(ValueError):
        read_completion(response(event({"choices": [{"delta": {"content": "{"}}]}) + ending))


def test_non_streaming_supplier_response_remains_supported():
    value = io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')
    value.headers = {"Content-Type": "application/json"}
    assert read_completion(value) == ("ok", None)


def test_stream_heartbeats_cannot_keep_request_alive_indefinitely(monkeypatch):
    ticks = iter([0, 241])
    monkeypatch.setattr("app.adapters.assist.streaming.time.monotonic", lambda: next(ticks))
    with pytest.raises(ValueError, match="时限"):
        read_completion(response(": heartbeat\n\n"))


def test_non_streaming_truncated_response_is_rejected():
    value = io.BytesIO(b'{"choices":[{"message":{"content":"{}"},"finish_reason":"length"}]}')
    value.headers = {"Content-Type": "application/json"}
    with pytest.raises(ValueError, match="未完整"):
        read_completion(value)


def test_preview_only_exposes_top_level_reply_not_tool_arguments():
    assert preview('{"kind":"finish","message":"hello') == "hello"
    assert preview('{"kind":"tool","arguments":{"message":"secret') == ""
    assert preview('{"kind":"plan","plan":{"message":"draft') == ""
    assert preview('{"kind":"finish","message":"a\\u4f') == "a"
    assert preview('{"kind":"finish","message":"a\\u4f60') == "a你"


def test_request_budget_preserves_decisions_and_never_mutates_original():
    packet = {
        "context": {"facts": {"user_decision": ["keep this"]}, "history": ["x" * 100000]},
        "tools": [],
        "steps": [],
    }
    actual = bounded(packet, "system")
    assert actual["context"]["history"] == []
    assert packet["context"]["history"]
    assert actual["context"]["facts"] == packet["context"]["facts"]
    with pytest.raises(CapabilityUnsupported):
        bounded({"context": {"facts": {"user_decision": ["x" * 100000]}}}, "system")


def test_discovery_schema_not_repeated_in_history():
    packet = {
        "steps": [
            {
                "receipt": {
                    "tool": "capabilities.discover",
                    "data": [{"name": "x", "input_schema": {"big": "x" * 100000}}],
                }
            }
        ]
    }
    assert "input_schema" not in bounded(packet, "")["steps"][0]["receipt"]["data"][0]
