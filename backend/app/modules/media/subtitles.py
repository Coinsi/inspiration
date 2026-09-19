"""Strict SRT parsing shared by library transcripts and timeline subtitles."""

import re

TIME = r"(\d{2,3}):(\d{2}):(\d{2})[,.](\d{3})"
RANGE = re.compile(rf"^{TIME}\s*-->\s*{TIME}$")


def parse_srt(content: str, duration_ms: int | None = None):
    if len(content) > 1_000_000 or "\ufffd" in content:
        raise ValueError("字幕过大或编码不正确，请使用 UTF-8 或选择正确编码")
    blocks = re.split(
        r"\n[ \t]*\n", content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    )
    if not content.strip() or len(blocks) > 10000:
        raise ValueError("请导入1–10000条字幕")
    items = []
    for ordinal, block in enumerate(blocks, 1):
        lines = block.strip().splitlines()
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        match = RANGE.fullmatch(lines[0].strip()) if lines else None
        if not match or len(lines) < 2:
            raise ValueError(f"第{ordinal}条字幕格式不正确")
        values = list(map(int, match.groups()))
        if any(values[i] > 59 for i in (1, 2, 5, 6)):
            raise ValueError(f"第{ordinal}条字幕时间格式不正确")
        times = [
            ((values[i] * 60 + values[i + 1]) * 60 + values[i + 2]) * 1000 + values[i + 3]
            for i in (0, 4)
        ]
        text = "\n".join(lines[1:]).strip()
        if not text or len(text) > 4000 or times[1] - times[0] < 100:
            raise ValueError(f"第{ordinal}条字幕内容或持续时间无效")
        if duration_ms is not None and times[1] > duration_ms:
            raise ValueError(f"第{ordinal}条字幕超出视频时长")
        items.append({"start_ms": times[0], "end_ms": times[1], "text": text})
    return sorted(items, key=lambda i: (i["start_ms"], i["end_ms"]))


def export_srt(items):
    def stamp(ms):
        seconds, milliseconds = divmod(ms, 1000)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"

    return (
        "\n\n".join(
            f"{n}\n{stamp(i['start_ms'])} --> {stamp(i['end_ms'])}\n{i['text']}"
            for n, i in enumerate(sorted(items, key=lambda i: (i["start_ms"], i["end_ms"])), 1)
        )
        + "\n"
    )
