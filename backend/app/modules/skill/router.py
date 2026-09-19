import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.core.errors import CapabilityUnsupported, Conflict, Forbidden, NotFound
from app.models.skill import CreativeSkill, CreativeSkillVersion
from app.modules.skill import schemas, service

router = APIRouter(prefix="/projects/{project_id}", tags=["skills"])
DB = Depends(get_db, scope="function")
READ = Depends(get_project_context)
EDIT = Depends(require_action("skill.edit"))
IMPORT = Depends(require_action("asset.edit"))
UPLOAD = File(...)


@router.get("/skills/templates")
def templates(ctx: ProjectContext = READ):
    return service.TEMPLATES


@router.get("/skills")
def listing(
    archived: bool = False,
    offset: int = Query(0, ge=0),
    ctx: ProjectContext = READ,
    db: Session = DB,
):
    return [
        service.out(s)
        for s in db.scalars(
            select(CreativeSkill)
            .where(CreativeSkill.project_id == ctx.project.id, CreativeSkill.archived == archived)
            .order_by(CreativeSkill.updated_at.desc(), CreativeSkill.id)
            .offset(offset)
            .limit(50)
        )
    ]


@router.post("/skills")
def create(data: schemas.SkillIn, ctx: ProjectContext = EDIT, db: Session = DB):
    return service.create(db, ctx, data)


@router.get("/skills/catalog")
def catalog(
    q: str = Query("", max_length=200),
    category: str = "",
    scope: Literal["project", "mine", "favorites"] = "project",
    sort: Literal["updated", "name"] = "updated",
    archived: bool = False,
    offset: int = Query(0, ge=0),
    ctx: ProjectContext = READ,
    db: Session = DB,
):
    from app.models.identity import Membership, Project

    statement = select(CreativeSkill).join(Project, Project.id == CreativeSkill.project_id)
    if scope in ("mine", "favorites"):
        statement = statement.join(Membership, Membership.project_id == Project.id).where(
            Membership.user_id == ctx.user.id
        )
        if scope == "mine":
            statement = statement.where(CreativeSkill.created_by == ctx.user.id)
        else:
            statement = statement.where(
                func.jsonb_exists(
                    Project.settings["skill_favorites"][str(ctx.user.id)],
                    cast(CreativeSkill.id, String),
                )
            )
    else:
        statement = statement.where(CreativeSkill.project_id == ctx.project.id)
    statement = statement.where(CreativeSkill.archived == archived, Project.deleted_at.is_(None))
    if category:
        statement = statement.where(
            func.coalesce(CreativeSkill.document["category"].as_string(), "general") == category
        )
    if q.strip():
        statement = statement.where(
            or_(
                CreativeSkill.name.icontains(q.strip(), autoescape=True),
                CreativeSkill.document["description"]
                .as_string()
                .icontains(q.strip(), autoescape=True),
                CreativeSkill.document["instructions"]
                .as_string()
                .icontains(q.strip(), autoescape=True),
            )
        )
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    order = CreativeSkill.name if sort == "name" else CreativeSkill.updated_at.desc()
    rows = list(db.scalars(statement.order_by(order, CreativeSkill.id).offset(offset).limit(24)))
    favorites = {
        p.id: set((p.settings or {}).get("skill_favorites", {}).get(str(ctx.user.id), []))
        for p in db.scalars(select(Project).where(Project.id.in_({r.project_id for r in rows})))
    }
    return {
        "total": total,
        "items": [
            {
                **{k: v for k, v in service.out(row).items() if k not in ("files", "instructions")},
                "project_id": str(row.project_id),
                "favorite": str(row.id) in favorites.get(row.project_id, set()),
                "file_count": len(row.document.get("files", [])) + 1,
            }
            for row in rows
        ],
    }


@router.post("/skills/install")
def install(data: schemas.Install, ctx: ProjectContext = EDIT, db: Session = DB):
    return service.install(db, ctx, data)


class FavoriteIn(BaseModel):
    favorite: bool


@router.put("/skills/{skill_id}/favorite")
def favorite(skill_id: uuid.UUID, data: FavoriteIn, ctx: ProjectContext = READ, db: Session = DB):
    from app.models.identity import Project

    service.get(db, ctx.project.id, skill_id)
    project = db.scalar(
        select(Project)
        .where(Project.id == ctx.project.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    all_favorites = dict((project.settings or {}).get("skill_favorites", {}))
    mine = set(all_favorites.get(str(ctx.user.id), []))
    if data.favorite:
        mine.add(str(skill_id))
    else:
        mine.discard(str(skill_id))
    all_favorites[str(ctx.user.id)] = sorted(mine)
    project.settings = {**(project.settings or {}), "skill_favorites": all_favorites}
    db.flush()
    return {"favorite": data.favorite}


@router.post("/skills/import-preview")
async def import_preview(file: UploadFile = UPLOAD, ctx: ProjectContext = EDIT):
    from app.modules.skill.packages import parse_package

    body = await file.read(32_000_001)
    return schemas.SkillIn(**parse_package(body, file.filename or "skill.zip"))


@router.post("/skills/github-preview")
def github_preview(data: schemas.GitHubImport, ctx: ProjectContext = EDIT):
    from app.modules.skill.packages import github_package

    return schemas.SkillIn(**github_package(data.url, data.ref, data.directory))


@router.post("/skills/copy")
def copy_skill(data: schemas.CopyIn, ctx: ProjectContext = EDIT, db: Session = DB):
    from app.models.identity import Membership, Project

    allowed = db.scalar(
        select(Membership)
        .join(Project, Project.id == Membership.project_id)
        .where(
            Membership.user_id == ctx.user.id,
            Membership.project_id == data.source_project_id,
            Project.deleted_at.is_(None),
        )
    )
    if not allowed:
        raise Forbidden("无权读取来源项目")
    origin = service.get(db, data.source_project_id, data.source_skill_id)
    if origin.archived or origin.revision != data.revision:
        raise Conflict("来源技能已变化，请重新读取")
    doc = {
        **origin.document,
        "source": "项目复用：" + origin.name,
        "source_metadata": {
            "kind": "copy",
            "origin_id": str(origin.id),
            "ref": str(origin.revision),
        },
    }
    return service.create(db, ctx, schemas.SkillIn(**doc))


@router.get("/skills/{skill_id}/bundle")
def download_bundle(skill_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB):
    from app.modules.skill.packages import bundle

    return Response(
        bundle(service.get(db, ctx.project.id, skill_id).document),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="skill-package.zip"'},
    )


@router.post("/skills/{skill_id}/sync-preview")
def sync_preview(
    skill_id: uuid.UUID, data: schemas.Restore, ctx: ProjectContext = EDIT, db: Session = DB
):
    from app.modules.skill.packages import github_package

    skill = service.get(db, ctx.project.id, skill_id)
    if skill.revision != data.revision:
        raise Conflict("技能已变化，请重新读取")
    meta = skill.document.get("source_metadata", {})
    if meta.get("kind") != "github":
        raise CapabilityUnsupported("仅GitHub来源可同步")
    doc = github_package(meta["url"], meta["ref"], meta.get("directory", ""))
    return {
        **schemas.SkillIn(**doc).model_dump(),
        "id": str(skill.id),
        "revision": skill.revision,
        "archived": skill.archived,
        "category": skill.document.get("category", "general"),
        "required_tools": skill.document.get("required_tools", []),
    }


@router.get("/skills/{skill_id}")
def detail(skill_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB):
    return service.out(service.get(db, ctx.project.id, skill_id))


@router.put("/skills/{skill_id}")
def save(skill_id: uuid.UUID, data: schemas.Save, ctx: ProjectContext = EDIT, db: Session = DB):
    return service.save(db, ctx, skill_id, data)


@router.post("/skills/{skill_id}/archive")
def archive(
    skill_id: uuid.UUID, data: schemas.Archive, ctx: ProjectContext = EDIT, db: Session = DB
):
    s = service.get(db, ctx.project.id, skill_id, True)
    if s.revision != data.revision:
        raise Conflict("技能已变化，请刷新后操作")
    s.archived = data.archived
    s.revision += 1
    service.record(db, ctx, s)
    return service.out(s)


@router.get("/skills/{skill_id}/history")
def history(
    skill_id: uuid.UUID, offset: int = Query(0, ge=0), ctx: ProjectContext = READ, db: Session = DB
):
    service.get(db, ctx.project.id, skill_id)
    return [
        {"revision": v.revision, "created_at": v.created_at, "document": v.document}
        for v in db.scalars(
            select(CreativeSkillVersion)
            .where(CreativeSkillVersion.skill_id == skill_id)
            .order_by(CreativeSkillVersion.revision.desc())
            .offset(offset)
            .limit(20)
        )
    ]


@router.post("/skills/{skill_id}/history/{version}/restore")
def restore(
    skill_id: uuid.UUID,
    version: int,
    data: schemas.Restore,
    ctx: ProjectContext = EDIT,
    db: Session = DB,
):
    service.get(db, ctx.project.id, skill_id, True)
    v = db.scalar(
        select(CreativeSkillVersion).where(
            CreativeSkillVersion.skill_id == skill_id, CreativeSkillVersion.revision == version
        )
    )
    if not v:
        raise NotFound("技能版本不存在")
    return service.save(db, ctx, skill_id, schemas.Save(**v.document, revision=data.revision))


@router.post("/source-catalogues/import")
def import_catalogue(
    data: schemas.ImportIn,
    ctx: ProjectContext = IMPORT,
    db: Session = DB,
):
    return service.import_catalogue(db, ctx, data)
