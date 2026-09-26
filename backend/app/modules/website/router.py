"""Website publication is separate from project permissions and project media."""

import re
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.errors import CapabilityUnsupported, Conflict, Forbidden, NotFound
from app.models.identity import User
from app.models.storage import Blob
from app.models.website import BlogPost, Website, WebsiteMedia, WebsiteRelease
from app.modules.website.schemas import Content, Publish, Restore, Save
from app.storage import cas

router = APIRouter(tags=["website"])
DB = Depends(get_db, scope="function")


def platform_admin(user: User = Depends(get_current_user)):
    if not user.is_platform_admin:
        raise Forbidden("仅平台管理员可管理官网，项目管理员没有此权限")
    return user


ADMIN = Depends(platform_admin)


def defaults():
    return Content.model_validate_json(
        Path(__file__).with_name("defaults.json").read_text(encoding="utf-8-sig")
    ).model_dump(mode="json")


def state(db, *, locked=False):
    if db.get(Website, 1) is None:
        data = defaults()
        db.execute(
            insert(Website)
            .values(id=1, version=1, published_revision=0, draft=data, published=data)
            .on_conflict_do_nothing(index_elements=[Website.id])
        )
    query = select(Website).where(Website.id == 1).execution_options(populate_existing=True)
    if locked:
        query = query.with_for_update()
    return db.scalar(query)


def out(row, db):
    return {
        "version": row.version,
        "published_revision": row.published_revision,
        "draft": row.draft,
        "published": row.published,
        "updated_at": row.updated_at,
        "media_types": types(db, row.draft),
    }


def media_ids(content, active_only=False):
    ids = set()
    enabled = {s["key"] for s in content["sections"] if s["enabled"]}
    if content.get("hero_media_id"):
        ids.add(content["hero_media_id"])
    if not active_only or "case" in enabled:
        if content.get("case_media_id"):
            ids.add(content["case_media_id"])
    if not active_only or "features" in enabled:
        ids.update(f["media_id"] for f in content["features"] if f.get("media_id"))
    return ids


def validate_media(db, content, verify_storage=False):
    for value in media_ids(content):
        media = db.get(WebsiteMedia, uuid.UUID(value))
        if media is None:
            raise CapabilityUnsupported("官网素材不存在，请重新上传；不能直接引用私人项目文件")
        if verify_storage:
            blob = db.get(Blob, media.blob_hash)
            try:
                with cas.open_range(blob, 0, 1) as stream:
                    if not stream.read(1):
                        raise ValueError()
            except Exception as exc:
                raise CapabilityUnsupported("展示素材不可读取，未发布，请检查存储") from exc


def types(db, content):
    ids = [uuid.UUID(value) for value in media_ids(content)]
    return (
        {
            str(mid): mime
            for mid, mime in db.execute(
                select(WebsiteMedia.id, Blob.mime)
                .join(Blob, Blob.hash == WebsiteMedia.blob_hash)
                .where(WebsiteMedia.id.in_(ids))
            )
        }
        if ids
        else {}
    )


def match_version(row, version):
    if row.version != version:
        raise Conflict("内容已在其他窗口更新，请重新载入后再操作")


@router.get("/website")
def public_site(response: Response, db: Session = DB):
    row = db.get(Website, 1)
    response.headers["Cache-Control"] = "no-cache"
    return {
        "revision": row.published_revision if row else 0,
        "content": row.published if row else defaults(),
        "media_types": types(db, row.published) if row else {},
    }


@router.get("/admin/website")
def admin_site(user: User = ADMIN, db: Session = DB):
    return out(state(db), db)


@router.put("/admin/website/draft")
def save_draft(data: Save, user: User = ADMIN, db: Session = DB):
    row = state(db, locked=True)
    match_version(row, data.version)
    content = data.content.model_dump(mode="json")
    validate_media(db, content)
    row.draft = content
    row.version += 1
    db.flush()
    return out(row, db)


@router.post("/admin/website/publish")
def publish(data: Publish, user: User = ADMIN, db: Session = DB):
    row = state(db, locked=True)
    match_version(row, data.version)
    content = Content.model_validate(row.draft).model_dump(mode="json")
    validate_media(db, content, verify_storage=True)
    row.published_revision += 1
    row.version += 1
    row.published = content
    db.add(
        WebsiteRelease(
            revision=row.published_revision, content=content, note=data.note, created_by=user.id
        )
    )
    db.flush()
    return out(row, db)


@router.get("/admin/website/history")
def history(offset: int = Query(0, ge=0), user: User = ADMIN, db: Session = DB):
    return [
        {"revision": r.revision, "note": r.note, "created_at": r.created_at, "author": name}
        for r, name in db.execute(
            select(WebsiteRelease, User.display_name)
            .join(User, User.id == WebsiteRelease.created_by)
            .order_by(WebsiteRelease.revision.desc())
            .offset(offset)
            .limit(20)
        )
    ]


@router.post("/admin/website/history/{revision}/restore")
def restore(revision: int, data: Restore, user: User = ADMIN, db: Session = DB):
    row = state(db, locked=True)
    match_version(row, data.version)
    release = db.get(WebsiteRelease, revision)
    if release is None:
        raise NotFound("发布记录不存在")
    row.draft = release.content
    row.version += 1
    db.flush()
    return out(row, db)


@router.get("/admin/website/media")
def media_list(offset: int = Query(0, ge=0), user: User = ADMIN, db: Session = DB):
    return [
        {"id": m.id, "name": m.name, "mime": b.mime, "bytes": b.size_bytes}
        for m, b in db.execute(
            select(WebsiteMedia, Blob)
            .join(Blob, Blob.hash == WebsiteMedia.blob_hash)
            .order_by(WebsiteMedia.created_at.desc(), WebsiteMedia.id)
            .offset(offset)
            .limit(30)
        )
    ]


@router.post("/admin/website/media")
def upload_media(file: UploadFile = File(...), user: User = ADMIN, db: Session = DB):
    with tempfile.TemporaryDirectory(prefix="website-upload-") as tmp:
        path = Path(tmp) / "upload"
        size = 0
        with path.open("wb") as target:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > 80 * 1024 * 1024:
                    raise CapabilityUnsupported("官网展示文件不能超过80 MB")
                target.write(chunk)
        try:
            with path.open("rb") as stream:
                header = stream.read(12)
            if header[4:8] == b"ftyp":
                from app.modules.generation.media_engine import run

                run(
                    [
                        "-protocol_whitelist",
                        "file,pipe",
                        "-i",
                        str(path),
                        "-map",
                        "0:v:0",
                        "-t",
                        "0.1",
                        "-f",
                        "null",
                        "-",
                    ],
                    timeout=30,
                )
                mime = "video/mp4"
            else:
                with Image.open(path) as image:
                    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(
                        image.format
                    )
                    if (
                        not mime
                        or image.width * image.height > 40_000_000
                        or getattr(image, "is_animated", False)
                    ):
                        raise ValueError()
                    image.load()
        except Exception as exc:
            raise CapabilityUnsupported("请选择有效的静态 JPG、PNG、WebP 图片或 MP4 视频") from exc
        blob = cas.put_file(db, path, mime)
        media = WebsiteMedia(
            blob_hash=blob.hash,
            name=Path(file.filename or "展示素材").name[:180],
            created_by=user.id,
        )
        db.add(media)
        db.flush()
        return {"id": media.id, "name": media.name, "mime": mime, "bytes": size}


def stream_media(db, media_id, range_value):
    media = db.get(WebsiteMedia, media_id)
    if media is None:
        raise NotFound("展示素材不存在")
    blob = db.get(Blob, media.blob_hash)
    size = blob.size_bytes
    start, end = 0, size - 1
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if range_value:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_value)
        if not match or not any(match.groups()):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        left, right = match.groups()
        start = int(left) if left else max(0, size - int(right))
        end = min(int(right), size - 1) if left and right else size - 1
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = end - start + 1
    mime = blob.mime
    db.expunge(blob)
    db.commit()
    manager = cas.open_range(blob, start, length)
    try:
        stream = manager.__enter__()
    except Exception as exc:
        raise NotFound("展示素材暂不可读取") from exc
    closed = False

    def close():
        nonlocal closed
        if not closed:
            closed = True
            manager.__exit__(None, None, None)

    def chunks():
        remaining = length
        try:
            while remaining > 0:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            close()

    headers["Content-Length"] = str(length)
    return StreamingResponse(
        chunks(),
        media_type=mime,
        headers=headers,
        status_code=206 if range_value else 200,
        background=BackgroundTask(close),
    )


@router.get("/website/media/{media_id}")
def public_media(
    media_id: uuid.UUID, range_value: str | None = Header(None, alias="Range"), db: Session = DB
):
    row = db.get(Website, 1)
    website_uses = row and str(media_id) in media_ids(row.published, True)
    blog_uses = db.scalar(select(BlogPost.id).where(
        BlogPost.archived.is_(False), BlogPost.published.is_not(None),
        BlogPost.published["cover_media_id"].astext == str(media_id),
    ).limit(1))
    if not website_uses and not blog_uses:
        raise NotFound("展示素材未发布")
    return stream_media(db, media_id, range_value)


@router.get("/admin/website/media/{media_id}")
def private_media(
    media_id: uuid.UUID,
    range_value: str | None = Header(None, alias="Range"),
    user: User = ADMIN,
    db: Session = DB,
):
    return stream_media(db, media_id, range_value)
