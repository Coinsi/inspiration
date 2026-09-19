"""Bounded directory tree and atomic media organization, independent of version identity."""

from sqlalchemy import func, select

from app.core import audit
from app.core.errors import Conflict, NotFound
from app.models.identity import Project
from app.models.library import LibraryMedia, MediaFolder

MAX_FOLDERS = 1000
MAX_DEPTH = 8


def lock_project(db, project_id):
    db.execute(select(Project.id).where(Project.id == project_id).with_for_update()).all()


def get(db, project_id, folder_id):
    if folder_id is None:
        return None
    item = db.scalar(
        select(MediaFolder).where(MediaFolder.id == folder_id, MediaFolder.project_id == project_id)
    )
    if item is None:
        raise NotFound("素材目录不存在")
    return item


def listing(db, project_id):
    counts = dict(
        db.execute(
            select(LibraryMedia.folder_id, func.count())
            .where(LibraryMedia.project_id == project_id, LibraryMedia.deleted_at.is_(None))
            .group_by(LibraryMedia.folder_id)
        ).all()
    )
    return [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id,
            "revision": f.revision,
            "media_count": counts.get(f.id, 0),
        }
        for f in db.scalars(
            select(MediaFolder)
            .where(MediaFolder.project_id == project_id)
            .order_by(MediaFolder.name, MediaFolder.id)
            .limit(MAX_FOLDERS)
        )
    ]


def save(db, ctx, data, folder_id=None):
    lock_project(db, ctx.project.id)
    # Re-read under the project lock; every directory mutation uses this lock.
    tree = list(
        db.scalars(
            select(MediaFolder)
            .where(MediaFolder.project_id == ctx.project.id)
            .execution_options(populate_existing=True)
        )
    )
    by_id = {f.id: f for f in tree}
    item = by_id.get(folder_id) if folder_id else None
    if folder_id and not item:
        raise NotFound("素材目录不存在")
    if item and item.revision != data.revision:
        raise Conflict("目录已被修改，请刷新后再试")
    if data.parent_id is not None and data.parent_id not in by_id:
        raise NotFound("父目录不存在")
    name = data.name.strip()
    if not name or name in (".", "..") or any(c in name for c in ("/", "\\", "\x00")):
        raise Conflict("请输入有效目录名称，不包含斜杠")
    if any(
        f.id != folder_id and f.parent_id == data.parent_id and f.name.casefold() == name.casefold()
        for f in tree
    ):
        raise Conflict("同一层已有这个名称的目录")
    if not item and len(tree) >= MAX_FOLDERS:
        raise Conflict("单个项目最多1000个素材目录")
    # Check ancestors and the whole moved subtree before changing a parent.
    parents = {f.id: f.parent_id for f in tree}
    current_id = folder_id or "new"
    parents[current_id] = data.parent_id
    for start in parents:
        seen, current = set(), start
        while current is not None:
            if current in seen:
                raise Conflict("不能将目录移入自己或自己的子目录")
            seen.add(current)
            if len(seen) > MAX_DEPTH:
                raise Conflict("素材目录最多8层")
            current = parents.get(current)
    if item:
        item.name, item.parent_id = name, data.parent_id
        item.revision += 1
    else:
        item = MediaFolder(project_id=ctx.project.id, name=name, parent_id=data.parent_id)
        db.add(item)
    db.flush()
    audit.record(
        db,
        action="library.folder.save",
        project_id=ctx.project.id,
        user_id=ctx.user.id,
        target_type="media_folder",
        target_id=item.id,
        detail={"name": name, "revision": item.revision},
    )
    return {
        "id": item.id,
        "name": item.name,
        "parent_id": item.parent_id,
        "revision": item.revision,
    }


def remove(db, ctx, folder_id, revision):
    lock_project(db, ctx.project.id)
    item = get(db, ctx.project.id, folder_id)
    db.refresh(item)
    if item.revision != revision:
        raise Conflict("目录已被修改，请刷新后再试")
    if db.scalar(
        select(MediaFolder.id).where(MediaFolder.parent_id == folder_id).limit(1)
    ) or db.scalar(select(LibraryMedia.id).where(LibraryMedia.folder_id == folder_id).limit(1)):
        raise Conflict("目录内仍有子目录或素材（含回收站），请先移出，删除目录不会删除视频")
    db.delete(item)
    audit.record(
        db,
        action="library.folder.remove",
        project_id=ctx.project.id,
        user_id=ctx.user.id,
        target_id=folder_id,
    )
    return {"removed": True}


def move_media(db, ctx, data):
    lock_project(db, ctx.project.id)
    get(db, ctx.project.id, data.folder_id)
    expected = {i.media_id: i.expected_folder_id for i in data.items}
    if len(expected) != len(data.items):
        raise Conflict("选择的素材重复，请重新选择")
    items = list(
        db.scalars(
            select(LibraryMedia)
            .where(LibraryMedia.project_id == ctx.project.id, LibraryMedia.id.in_(expected))
            .order_by(LibraryMedia.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if len(items) != len(expected):
        raise NotFound("部分素材不存在或不属于当前项目，未移动任何素材")
    if any(i.folder_id not in (expected[i.id], data.folder_id) for i in items):
        raise Conflict("部分素材已被移动，请刷新并重新选择；未移动任何素材")
    for item in items:
        item.folder_id = data.folder_id
    audit.record(
        db,
        action="library.media.move",
        project_id=ctx.project.id,
        user_id=ctx.user.id,
        detail={
            "media_ids": [str(i.id) for i in items],
            "folder_id": str(data.folder_id) if data.folder_id else None,
        },
    )
    return {"moved": len(items)}
