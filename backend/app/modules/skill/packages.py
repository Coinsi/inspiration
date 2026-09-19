"""Bounded skill packages; files are data, never executed or extracted to disk."""

import base64
import io
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import yaml

from app.core.errors import CapabilityUnsupported


def safe_path(value):
    parts = value.split("/")
    if (
        not value
        or len(value) > 240
        or "\\" in value
        or ":" in value
        or any(p in ("", ".", "..") for p in parts)
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("技能包文件路径无效")
    return value


def _text(data):
    try:
        value = data.decode("utf-8-sig")
        if "\x00" in value:
            raise ValueError()
        return value
    except (UnicodeError, ValueError):
        raise CapabilityUnsupported("SKILL.md 必须是 UTF-8 文本") from None


def parse_package(payload, filename, directory=""):
    if len(payload) > 32_000_000:
        raise CapabilityUnsupported("压缩包超过32MB")
    files = {}
    if filename.lower().endswith((".md", ".markdown", ".txt")):
        files["SKILL.md"] = payload
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as z:
                entries = z.infolist()
                if len(entries) > 10000:
                    raise ValueError("压缩包条目过多")
                roots = [
                    e.filename
                    for e in entries
                    if e.filename.endswith("/SKILL.md") or e.filename == "SKILL.md"
                ]
                if directory:
                    safe_path(directory)
                    roots = [
                        r
                        for r in roots
                        if r == directory + "/SKILL.md" or r.endswith("/" + directory + "/SKILL.md")
                    ]
                if len(roots) != 1:
                    raise ValueError("请提供含唯一SKILL.md的技能目录；多个入口时请指定子目录")
                safe_path(roots[0])
                prefix = roots[0][:-8]
                total = 0
                for e in entries:
                    if e.is_dir() or not e.filename.startswith(prefix):
                        continue
                    path = safe_path(e.filename[len(prefix) :])
                    if stat.S_ISLNK(e.external_attr >> 16) or e.flag_bits & 1:
                        raise ValueError("不支持链接或加密文件")
                    if e.file_size > 1_000_000:
                        raise ValueError("单个文件不能超过1MB")
                    total += e.file_size
                    if total > 4_000_000 or len(files) >= 41:
                        raise ValueError("技能包最多41个文件，总量不能超过4MB")
                    if path.casefold() in {p.casefold() for p in files}:
                        raise ValueError("技能包包含重名文件")
                    files[path] = z.read(e)
        except (zipfile.BadZipFile, RuntimeError, ValueError, NotImplementedError) as e:
            raise CapabilityUnsupported(str(e)) from e
    entry = _text(files.pop("SKILL.md"))
    if not entry.strip() or len(entry) > 20000:
        raise CapabilityUnsupported("入口说明须为1至20000字")
    metadata = {}
    body = entry
    if entry.startswith("---\n") or entry.startswith("---\r\n"):
        parts = re.split(r"^---\s*$", entry, maxsplit=2, flags=re.M)
        if len(parts) == 3:
            try:
                metadata = yaml.safe_load(parts[1]) or {}
                if not isinstance(metadata, dict):
                    raise ValueError()
                if any(not isinstance(metadata.get(k, ""), str) for k in ("name", "description")):
                    raise ValueError()
                body = parts[2]
            except (yaml.YAMLError, ValueError):
                raise CapabilityUnsupported("SKILL.md 元信息格式无效") from None
    title = re.search(r"^# +(.+)", body, re.M)
    name = str(metadata.get("name") or (title[1] if title else PurePosixPath(filename).stem))[:120]
    description = str(
        metadata.get("description")
        or next(
            (
                line.strip()
                for line in body.splitlines()
                if line.strip() and not line.startswith("#")
            ),
            "导入的创作方法",
        )
    )[:1000]
    from app.modules.agent.tools import CATALOG

    required_tools = metadata.get("inspiration_required_tools", [])
    if (
        not isinstance(required_tools, list)
        or len(required_tools) > 16
        or any(not isinstance(tool, str) or tool not in CATALOG for tool in required_tools)
    ):
        raise CapabilityUnsupported("技能包声明的工具依赖无效，请核对 inspiration_required_tools")
    attachments = []
    for path, data in files.items():
        try:
            content = data.decode("utf-8")
            if "\x00" in content:
                raise UnicodeError()
            encoding = "utf-8"
        except UnicodeError:
            content, encoding = base64.b64encode(data).decode(), "base64"
        attachments.append({"path": path, "content": content, "encoding": encoding})
    return {
        "name": name,
        "description": description,
        "instructions": entry,
        "files": attachments,
        "category": metadata.get("category")
        if metadata.get("category") in ("general", "story", "character", "shot", "edit")
        else "general",
        "required_tools": required_tools,
        "source": "文件导入：" + filename[:200],
        "source_metadata": {"kind": "file"},
    }


def github_package(url, ref="", directory=""):
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.query
        or parsed.fragment
    ):
        raise CapabilityUnsupported("请输入公开的 https://github.com 仓库或目录地址")
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts[:2]):
        raise CapabilityUnsupported("GitHub 仓库地址无效")
    owner, repo = parts[:2]
    repo = repo.removesuffix(".git")
    if len(parts) > 2:
        if len(parts) < 4 or parts[2] != "tree":
            raise CapabilityUnsupported("请使用仓库根地址或tree目录地址")
        ref = ref or parts[3]
        directory = directory or "/".join(parts[4:])
    if directory:
        try:
            safe_path(directory)
        except ValueError as e:
            raise CapabilityUnsupported(str(e)) from e

    def fetch(address, limit):
        req = Request(
            address,
            headers={
                "User-Agent": "Inspiration-Skill-Importer",
                "Accept": "application/vnd.github+json",
            },
        )
        with urlopen(req, timeout=20) as response:
            final = urlparse(response.url)
            if final.scheme != "https" or final.hostname not in {
                "api.github.com",
                "codeload.github.com",
            }:
                raise ValueError("GitHub 返回了不支持的下载地址")
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError("GitHub 返回内容超过大小限制")
            return data

    try:
        if not ref:
            ref = json.loads(fetch(f"https://api.github.com/repos/{owner}/{repo}", 200000))[
                "default_branch"
            ]
        commit = json.loads(
            fetch(
                f"https://api.github.com/repos/{owner}/{repo}/commits/{quote(ref, safe='')}",
                2_000_000,
            )
        )["sha"]
        if not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise ValueError("无法解析提交版本")
        payload = fetch(f"https://codeload.github.com/{owner}/{repo}/zip/{commit}", 32_000_000)
    except Exception as e:
        raise CapabilityUnsupported(
            "GitHub 读取失败，请核对公开仓库、分支、目录与网络后重试"
        ) from e
    doc = parse_package(payload, repo + ".zip", directory)
    doc["source"] = f"GitHub：{owner}/{repo}"
    doc["source_metadata"] = {
        "kind": "github",
        "url": f"https://github.com/{owner}/{repo}",
        "ref": ref,
        "directory": directory,
        "commit": commit,
    }
    return doc


def bundle(document):
    out = io.BytesIO()
    entry = document["instructions"]
    metadata = {}
    if entry.startswith(("---\n", "---\r\n")):
        parts = re.split(r"^---\s*$", entry, maxsplit=2, flags=re.M)
        if len(parts) == 3:
            try:
                parsed = yaml.safe_load(parts[1])
                if isinstance(parsed, dict):
                    metadata = parsed
                    entry = parts[2].lstrip("\r\n")
            except yaml.YAMLError:
                pass
    metadata.update(
        name=document["name"],
        description=document["description"],
        category=document.get("category", "general"),
        inspiration_required_tools=document.get("required_tools", []),
    )
    entry = (
        "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n" + entry
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("SKILL.md", entry)
        for f in document.get("files", []):
            z.writestr(
                safe_path(f["path"]),
                base64.b64decode(f["content"])
                if f["encoding"] == "base64"
                else f["content"].encode(),
            )
    return out.getvalue()
