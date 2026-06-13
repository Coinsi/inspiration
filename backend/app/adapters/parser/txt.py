"""txt 纯文本小说解析:按章节标题行切分。"""
import re
from typing import BinaryIO

from app.adapters.contracts import Chapter, NovelDocument, NovelParser, parser_registry

# 匹配常见章节标题:第1章/第一章/第1回/Chapter 1/卷X 等
CHAPTER_RE = re.compile(
    r"^\s*(第\s*[0-9零一二三四五六七八九十百千]+\s*[章回节]|Chapter\s+\d+|CHAPTER\s+\d+)\b.*$",
    re.IGNORECASE,
)


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


@parser_registry.register("txt")
class TxtParser(NovelParser):
    name = "txt"

    def can_handle(self, filename: str, mime: str) -> bool:
        return filename.lower().endswith(".txt") or mime in ("text/plain",)

    def parse(self, file: BinaryIO) -> NovelDocument:
        text = _decode(file.read())
        lines = text.splitlines()
        chapters: list[Chapter] = []
        title: str | None = None
        buf: list[str] = []

        def flush(ordinal: int):
            if title is not None or buf:
                chapters.append(
                    Chapter(ordinal=ordinal, title=title, content="\n".join(buf).strip())
                )

        for line in lines:
            if CHAPTER_RE.match(line):
                if title is not None or buf:
                    flush(len(chapters) + 1)
                title = line.strip()
                buf = []
            else:
                buf.append(line)
        flush(len(chapters) + 1)

        # 无任何章节标题 → 整篇作为一章
        if not chapters:
            chapters = [Chapter(ordinal=1, title="正文", content=text.strip())]
        # 纯文本无书名信息;留空交由 import_novel 用文件名兜底
        return NovelDocument(title="", chapters=chapters)
