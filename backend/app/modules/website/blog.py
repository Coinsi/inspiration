"""Public articles read only published snapshots; every mutation requires a platform admin."""

import math
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Query, Response
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.identity import User
from app.models.storage import Blob
from app.models.website import BlogPost, WebsiteMedia
from app.modules.website.router import ADMIN, DB, match_version
from app.modules.website.schemas import Strict
from app.storage import cas

router = APIRouter(tags=["blog"])
Category = Literal["创作指南", "产品更新", "灵感笔记"]


class Article(Strict):
    title: str = Field(min_length=1, max_length=120)
    excerpt: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=100000)
    category: Category = "创作指南"
    author: str = Field(default="Inspiration 编辑部", min_length=1, max_length=80)
    cover_media_id: uuid.UUID | None = None


class Create(Strict):
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    content: Article


class Save(Strict):
    version: int = Field(ge=1)
    content: Article


class Change(Strict):
    version: int = Field(ge=1)


def get_post(db, post_id):
    row = db.scalar(select(BlogPost).where(BlogPost.id == post_id).with_for_update())
    if row is None:
        raise NotFound("文章不存在")
    return row


def check_cover(db, content, verify=False):
    media_id = content.get("cover_media_id")
    if not media_id:
        return
    media = db.get(WebsiteMedia, uuid.UUID(media_id))
    blob = db.get(Blob, media.blob_hash) if media else None
    if not blob or blob.mime not in {"image/jpeg", "image/png", "image/webp"}:
        raise CapabilityUnsupported("请选择官网素材库中的 JPG、PNG 或 WebP 图片作为封面")
    if verify:
        try:
            with cas.open_range(blob, 0, 1) as stream:
                if not stream.read(1):
                    raise ValueError()
        except Exception as exc:
            raise CapabilityUnsupported("文章封面暂不可读取，未发布") from exc


def admin_out(row):
    return {
        "id": row.id,
        "slug": row.slug,
        "version": row.version,
        "draft": row.draft,
        "published": row.published,
        "published_at": row.published_at,
        "updated_at": row.updated_at,
        "archived": row.archived,
    }


def public_out(row, detail=False):
    content = dict(row.published)
    content["reading_minutes"] = max(1, math.ceil(len(content["body"]) / 650))
    if not detail:
        content.pop("body")
    return {"slug": row.slug, "published_at": row.published_at, **content}


@router.get("/blog")
def public_list(
    response: Response,
    q: str = Query("", max_length=150),
    category: Category | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(9, ge=1, le=30),
    db: Session = DB,
):
    response.headers["Cache-Control"] = "no-cache"
    filters = [BlogPost.published.is_not(None), BlogPost.archived.is_(False)]
    if category:
        filters.append(BlogPost.published["category"].astext == category)
    if q.strip():
        filters.append(
            or_(
                *(
                    BlogPost.published[k].astext.icontains(q.strip(), autoescape=True)
                    for k in ("title", "excerpt", "body")
                )
            )
        )
    total = db.scalar(select(func.count()).select_from(BlogPost).where(*filters))
    rows = db.scalars(
        select(BlogPost)
        .where(*filters)
        .order_by(BlogPost.published_at.desc(), BlogPost.id)
        .offset(offset)
        .limit(limit)
    )
    return {"items": [public_out(row) for row in rows], "total": total}


@router.get("/blog/{slug}")
def public_detail(slug: str, response: Response, db: Session = DB):
    response.headers["Cache-Control"] = "no-cache"
    row = db.scalar(
        select(BlogPost).where(
            BlogPost.slug == slug, BlogPost.published.is_not(None), BlogPost.archived.is_(False)
        )
    )
    if row is None:
        raise NotFound("文章尚未发布或已下线")
    return public_out(row, True)


@router.get("/admin/blog")
def admin_list(
    offset: int = Query(0, ge=0), archived: bool = False, db: Session = DB, user: User = ADMIN
):
    filters = [BlogPost.archived == archived]
    rows = db.scalars(
        select(BlogPost)
        .where(*filters)
        .order_by(BlogPost.updated_at.desc(), BlogPost.id)
        .offset(offset)
        .limit(20)
    )
    return {
        "items": [
            {k: v for k, v in admin_out(row).items() if k not in {"draft", "published"}}
            | {
                "title": row.draft["title"],
                "category": row.draft["category"],
                "live": row.published is not None,
                "has_changes": row.draft != row.published,
            }
            for row in rows
        ],
        "total": db.scalar(select(func.count()).select_from(BlogPost).where(*filters)),
    }


@router.post("/admin/blog", status_code=201)
def create(data: Create, db: Session = DB, user: User = ADMIN):
    content = data.content.model_dump(mode="json")
    check_cover(db, content)
    row = BlogPost(slug=data.slug, draft=content, created_by=user.id)
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("这个文章地址已被使用，请更换；归档文章仍保留地址") from exc
    return admin_out(row)


@router.get("/admin/blog/{post_id}")
def admin_detail(post_id: uuid.UUID, db: Session = DB, user: User = ADMIN):
    return admin_out(get_post(db, post_id))


@router.put("/admin/blog/{post_id}")
def save(post_id: uuid.UUID, data: Save, db: Session = DB, user: User = ADMIN):
    row = get_post(db, post_id)
    match_version(row, data.version)
    if row.archived:
        raise Conflict("请先恢复归档文章")
    content = data.content.model_dump(mode="json")
    check_cover(db, content)
    row.draft = content
    row.version += 1
    db.flush()
    return admin_out(row)


@router.post("/admin/blog/{post_id}/{action}")
def change(
    post_id: uuid.UUID,
    action: Literal["publish", "unpublish", "archive", "restore"],
    data: Change,
    db: Session = DB,
    user: User = ADMIN,
):
    row = get_post(db, post_id)
    match_version(row, data.version)
    if action == "restore":
        row.archived = False
    elif action == "archive":
        row.archived = True
        row.published = None
    elif action == "unpublish":
        row.published = None
    else:
        if row.archived:
            raise Conflict("请先恢复归档文章")
        content = Article.model_validate(row.draft).model_dump(mode="json")
        if len(content["body"].strip()) < 20 or not content["excerpt"].strip():
            raise CapabilityUnsupported("发布前请填写摘要及至少20个字符的正文")
        check_cover(db, content, True)
        row.published = content
        row.published_at = row.published_at or datetime.now(UTC)
    row.version += 1
    db.flush()
    return admin_out(row)
