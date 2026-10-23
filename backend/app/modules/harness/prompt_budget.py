"""Bound the complete text request, not only its narrative context."""

import copy
import json

from app.core.errors import CapabilityUnsupported

MAX_BYTES = 96000
MAX_TOOLS = 4


def brief(tool):
    return {
        k: tool.get(k) for k in ("name", "description", "group", "available", "unavailable_reason")
    }


def bounded(packet, system):
    value = copy.deepcopy(packet)
    removed = 0

    def size():
        return len((system + json.dumps(value, ensure_ascii=False)).encode("utf-8"))

    # Discovery schemas already reside in tools. Do not duplicate them in receipts.
    for step in value.get("steps", []):
        receipt = step.get("receipt", {})
        if receipt.get("tool") == "capabilities.discover" and isinstance(receipt.get("data"), list):
            receipt["data"] = [brief(t) for t in receipt["data"]]
    while size() > MAX_BYTES and value.get("steps"):
        value["steps"].pop(0)
        removed += 1
    history = value.get("context", {}).get("history", [])
    while size() > MAX_BYTES and history:
        history.pop(0)
        removed += 1
    if removed:
        value["prompt_omissions"] = {"older_steps_or_history": removed}
    # Never silently discard current user decisions, dependencies or fixed references.
    if size() > MAX_BYTES:
        raise CapabilityUnsupported("当前任务上下文超过请求预算，请缩小读取范围或拆分任务")
    return value
