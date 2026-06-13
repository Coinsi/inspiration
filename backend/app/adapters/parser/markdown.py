"""Markdown 小说解析:按 # / ## 标题切分章节。"""
import re
from typing import BinaryIO

from app.adapters.contracts import Chapter, NovelDocument, NovelParser, parser_registry

HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")


@parser_registry.register("markdown")
class MarkdownParser(NovelParser):
    name = "markdown"

    def can_handle(self, filename: str, mime: str) -> bool:
        return filename.lower().endswith((".md", ".markdown")) or mime in ("text/markdown",)

    def parse(self, file: BinaryIO) -> NovelDocument:
        text = file.read().decode("utf-8", errors="replace")
        chapters: list[Chapter] = []
        doc_title = ""  # 无一级标题时留空,交由 import_novel 用文件名兜底
        title: str | None = None
        buf: list[str] = []

        def flush():
            if title is not None or buf:
                chapters.append(
                    Chapter(ordinal=len(chapters) + 1, title=title, content="\n".join(buf).strip())
                )

        for line in text.splitlines():
            m = HEADING_RE.match(line)
            if m:
                level, heading = len(m.group(1)), m.group(2).strip()
                if level == 1 and not chapters and title is None and not buf:
                    doc_title = heading  # 一级标题作为书名
                    continue
                flush()
                title = heading
                buf = []
            else:
                buf.append(line)
        flush()

        if not chapters:
            chapters = [Chapter(ordinal=1, title="正文", content=text.strip())]
        return NovelDocument(title=doc_title, chapters=chapters)
