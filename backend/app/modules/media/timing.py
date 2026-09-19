"""Shared duration rules for overlapping picture transitions."""

TRANSITIONS = {"dissolve": "fade", "fadeblack": "fadeblack", "wipeleft": "wipeleft"}


def duration(items):
    spans = [
        i["out_point_ms"] - i.get("in_point_ms", 0)
        if i.get("out_point_ms")
        else i.get("duration_ms", 0)
        for i in items
    ]
    if any(n < 0 for n in spans):
        raise ValueError("画面时长不能为负数")
    total = sum(spans)
    for n, item in enumerate(items):
        transition = item.get("transition")
        if not transition:
            continue
        if not isinstance(transition, dict) or transition.get("type") not in TRANSITIONS:
            raise ValueError("请选择叠化、淡黑或向左擦除转场")
        ms = transition.get("duration_ms")
        if isinstance(ms, bool) or not isinstance(ms, int) or not 100 <= ms <= 2000:
            raise ValueError("转场时长须在0.1–2秒之间")
        if n == len(items) - 1:
            raise ValueError("最后一个画面没有后续转场")
        if ms * 2 > min(spans[n], spans[n + 1]):
            raise ValueError("转场不能超过相邻较短画面的一半时长")
        total -= ms
    return total
