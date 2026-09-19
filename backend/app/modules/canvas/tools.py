"""Canvas-local import and portable, authenticated export."""

import hashlib
import html
import json
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

from sqlalchemy import select

from app.core.errors import CapabilityUnsupported, Conflict
from app.core.permissions import require
from app.models.generation import Generation
from app.models.storage import Blob
from app.modules.asset import schemas as assets
from app.modules.asset import service as asset_service
from app.modules.canvas import service
from app.modules.generation.router import upload_media
from app.storage import cas


def import_file(db, ctx, canvas_id, key, file, source_id=None):
    require(ctx.role, "asset.edit")
    require(ctx.role, "generation.trigger")
    service.get_canvas(db, ctx.project.id, canvas_id, True)
    raw = file.file.read(100 * 1024 * 1024 + 1)
    if not raw or len(raw) > 100 * 1024 * 1024:
        raise CapabilityUnsupported("单文件最多 100 MB；大视频请使用素材库")
    digest = hashlib.sha256(raw).hexdigest()
    original = service.jobs.source(db, ctx.project.id, source_id, True) if source_id else None
    if original and original.target_type not in ("asset", "shot"):
        raise CapabilityUnsupported("请选择资产或镜头图片")
    previous = db.scalar(
        select(Generation).where(
            Generation.project_id == ctx.project.id,
            Generation.input_refs["canvas_import"].astext == str(canvas_id),
            Generation.input_refs["request_key"].astext == str(key),
        )
    )
    if previous:
        if previous.input_refs["file_digest"] != digest or previous.input_refs.get(
            "source_generation_id"
        ) != (str(source_id) if source_id else None):
            raise Conflict("同一导入请求不能更换文件")
        return previous
    file.file.seek(0)
    title = Path((file.filename or "粘贴图片").replace("\\", "/")).stem[:110] or "画布素材"
    asset = None
    if original:
        result = upload_media(original.target_type, original.target_id, file=file, ctx=ctx, db=db)
    else:
        asset = asset_service.create(
            db,
            ctx,
            assets.AssetIn(
                type="style", name=title, tags=["画布素材"], metadata={"canvas_id": str(canvas_id)}
            ),
        )
        result = upload_media("asset", asset.id, file=file, ctx=ctx, db=db)
    result.input_refs = {
        **result.input_refs,
        "canvas_import": str(canvas_id),
        "request_key": str(key),
        "file_digest": digest,
    }
    if original:
        result.input_refs = {
            **result.input_refs,
            "operation": "canvas_edit",
            "source_generation_id": str(original.id),
            "source_blob": original.output_blob_hash,
        }
    if asset and result.output_type == "image":
        asset.representative_blob_hash = result.output_blob_hash
    db.flush()
    return result


def bundle(db, ctx, canvas_id):
    canvas = service.get_canvas(db, ctx.project.id, canvas_id)
    doc = service.validate(db, ctx.project.id, canvas.document)
    latest = db.scalar(
        select(service.CanvasRun)
        .where(
            service.CanvasRun.canvas_id == canvas.id,
            service.CanvasRun.revision == canvas.revision,
        )
        .order_by(service.CanvasRun.created_at.desc())
        .limit(1)
    )
    records, blobs, cards = {}, {}, []
    nodes = {n["id"]: n for n in doc["nodes"]}
    positions = {}
    for node in nodes.values():
        p = node["position"].copy()
        if node.get("parentId"):
            parent = nodes[node["parentId"]]["position"]
            p = {"x": p["x"] + parent["x"], "y": p["y"] + parent["y"]}
        positions[node["id"]] = p
    left = min((p["x"] for p in positions.values()), default=0) - 40
    top = min((p["y"] for p in positions.values()), default=0) - 40
    width = (
        max((positions[n["id"]]["x"] + n["width"] - left for n in nodes.values()), default=800) + 40
    )
    height = (
        max((positions[n["id"]]["y"] + n["height"] - top for n in nodes.values()), default=600) + 40
    )
    if width > 32000 or height > 32000:
        raise CapabilityUnsupported("画布跨度过大，请整理布局后再导出")
    for node in nodes.values():
        d = node["data"]
        resolved = service.resolve(db, ctx.project.id, node)
        gid = d.get("generation_id")
        if not gid and d["kind"] in ("asset", "shot") and d.get("target_id"):
            target = service.jobs.validate_target(
                db, ctx.project.id, d["kind"], uuid.UUID(d["target_id"])
            )
            adopted = getattr(target, "selected_generation_id", None)
            gid = str(adopted) if adopted else None
        if not gid and d["kind"] == "generate" and latest:
            gid = latest.steps.get(node["id"], {}).get("output", {}).get("generation_id")
        g = service.jobs.source(db, ctx.project.id, uuid.UUID(gid)) if gid else None
        if g and (str(g.target_id) != d.get("target_id") or g.target_type != d["target_type"]):
            raise CapabilityUnsupported("导出素材归属不匹配")
        digest = (
            g.output_blob_hash
            if g
            else resolved.get("blob_hash")
            if d["kind"] != "generate"
            else None
        )
        media = ""
        if digest:
            blob = db.get(Blob, digest)
            if blob:
                ext = {
                    "image/png": "png",
                    "image/jpeg": "jpg",
                    "image/webp": "webp",
                    "video/mp4": "mp4",
                    "audio/wav": "wav",
                    "audio/mpeg": "mp3",
                }.get(blob.mime, "bin")
                relative = f"media/{digest}.{ext}"
                blobs[digest] = (blob, relative)
                records[node["id"]] = {"file": relative, "generation_id": str(g.id) if g else None}
                tag = (
                    "video"
                    if blob.mime.startswith("video/")
                    else "audio"
                    if blob.mime.startswith("audio/")
                    else "img"
                )
                media = (
                    f'<{tag} src="{relative}" '
                    + ('controls preload="metadata"' if tag != "img" else 'alt="作品"')
                    + f"></{tag}>"
                )
        p = positions[node["id"]]
        cls = "group" if d["kind"] == "group" else "node"
        cards.append(
            f'<article class="{cls}" style="left:{p["x"] - left}px;top:{p["y"] - top}px;width:{node["width"]}px;height:{node["height"]}px"><h3>{html.escape(d["label"])}</h3>{media}<p>{html.escape(d["text"])}</p></article>'
        )
    if sum(b.size_bytes for b, _ in blobs.values()) > 512 * 1024 * 1024:
        raise CapabilityUnsupported("作品包超过 512 MB，请拆分画布后导出")
    lines = []
    for e in doc["edges"]:
        a, b = nodes[e["source"]], nodes[e["target"]]
        ap, bp = positions[a["id"]], positions[b["id"]]
        lines.append(
            f'<path d="M {ap["x"] + a["width"] - left} {ap["y"] + a["height"] * 0.5 - top} L {bp["x"] - left} {bp["y"] + b["height"] * 0.5 - top}" stroke="{"#a78bfa" if e["sourceHandle"] == "image" else "#38bdf8"}"/>'
        )
    title = html.escape(canvas.name)
    page = f'''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;background:#101010;color:#eee;font:14px system-ui}}header{{position:sticky;top:0;z-index:5;background:#191919;padding:18px;display:flex;align-items:center;gap:20px}}header small{{color:#aaa}}#view{{overflow:auto;height:calc(100vh - 65px)}}#board{{position:relative;transform-origin:0 0;background-image:radial-gradient(#333 1px,transparent 1px);background-size:24px 24px}}article{{box-sizing:border-box;position:absolute;border:1px solid #444;border-radius:12px;padding:14px;background:#1b1b1b;overflow:auto}}h3{{font-size:14px;margin:0 0 10px}}article img,article video{{width:100%;max-height:75%;object-fit:contain}}audio{{width:100%}}p{{white-space:pre-wrap;font-size:13px;line-height:1.6}}.group{{background:#ffffff08;z-index:0}}.node{{z-index:1}}svg{{position:absolute;inset:0;pointer-events:none}}path{{fill:none;stroke-width:2}}</style>
<header><strong>{title}</strong><small>只读作品包 · 解压后离线查看</small><label>缩放 <input id="zoom" type="range" min="10" max="150" value="75"></label></header><div id="view"><div id="board" style="width:{width}px;height:{height}px;zoom:.75"><svg width="{width}" height="{height}">{"".join(lines)}</svg>{"".join(cards)}</div></div><script>document.getElementById('zoom').oninput=e=>document.getElementById('board').style.zoom=e.target.value/100;</script></html>'''
    fd, filename = tempfile.mkstemp(suffix=".zip", prefix="canvas-export-")
    os.close(fd)
    try:
        with zipfile.ZipFile(filename, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("index.html", page)
            archive.writestr(
                "canvas.json",
                json.dumps(
                    {
                        "name": canvas.name,
                        "revision": canvas.revision,
                        "document": doc,
                        "media": records,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            for blob, relative in blobs.values():
                with cas.open_range(blob) as source, archive.open(relative, "w") as dest:
                    shutil.copyfileobj(source, dest, 1024 * 1024)
        return filename
    except Exception:
        Path(filename).unlink(missing_ok=True)
        raise
