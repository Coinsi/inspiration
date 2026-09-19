import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.core import audit
from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.asset import Asset
from app.models.evidence import EvidenceRevision, IdentityEvidence, StoryIdentity
from app.models.library import LibraryMedia, MediaVersion
from app.modules.evidence import schemas
from app.modules.library import indexing
from app.modules.library import service as library
from app.modules.library.schemas import MediaSearchIn

router = APIRouter(prefix="/projects/{project_id}/identity-evidence", tags=["identity-evidence"])
DB = Depends(get_db, scope="function")
READ = Depends(get_project_context)
EDIT = Depends(require_action("asset.edit"))


def identity(db, ctx, id):
    r = db.scalar(
        select(StoryIdentity).where(
            StoryIdentity.id == id, StoryIdentity.project_id == ctx.project.id
        )
    )
    if not r:
        raise NotFound("角色身份不存在")
    return r


def asset(db, ctx, id):
    if id and not db.scalar(
        select(Asset.id).where(
            Asset.id == id,
            Asset.project_id == ctx.project.id,
            Asset.deleted_at.is_(None),
            Asset.type == "character",
        )
    ):
        raise NotFound("关联角色资产不存在")


def identout(i):
    return {
        k: (str(v) if isinstance(v, uuid.UUID) else v)
        for k in ("id", "name", "aliases", "description", "asset_id", "revision")
        for v in [getattr(i, k)]
    }


def payload(e):
    return {
        k: (str(v) if isinstance(v, uuid.UUID) else v)
        for k in (
            "id",
            "identity_id",
            "version_id",
            "start_ms",
            "end_ms",
            "kind",
            "status",
            "observation",
            "note",
            "source",
            "revision",
        )
        for v in [getattr(e, k)]
    }


def remember(db, ctx, e):
    db.add(
        EvidenceRevision(
            evidence_id=e.id, revision=e.revision, document=payload(e), created_by=ctx.user.id
        )
    )
    audit.record(
        db,
        action="evidence.save",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="evidence",
        target_id=e.id,
        detail={
            "revision": e.revision,
            "status": e.status,
            "identity_id": str(e.identity_id) if e.identity_id else None,
        },
    )


def get(db, ctx, id):
    r = db.scalar(
        select(IdentityEvidence).where(
            IdentityEvidence.id == id, IdentityEvidence.project_id == ctx.project.id
        )
    )
    if not r:
        raise NotFound("证据不存在")
    return r


@router.get("/identities")
def identities(
    q: str = Query("", max_length=120),
    offset: int = Query(0, ge=0),
    ctx: ProjectContext = READ,
    db: Session = DB,
):
    query = select(StoryIdentity).where(StoryIdentity.project_id == ctx.project.id)
    if q:
        query = query.where(StoryIdentity.name.icontains(q, autoescape=True))
    return [
        identout(i)
        for i in db.scalars(
            query.order_by(StoryIdentity.created_at.desc(), StoryIdentity.id)
            .offset(offset)
            .limit(100)
        )
    ]


@router.post("/identities")
def create_identity(data: schemas.IdentityIn, ctx: ProjectContext = EDIT, db: Session = DB):
    asset(db, ctx, data.asset_id)
    i = StoryIdentity(
        project_id=ctx.project.id, created_by=ctx.user.id, revision=0, **data.model_dump()
    )
    db.add(i)
    db.flush()
    audit.record(
        db,
        action="identity.create",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=i.id,
        detail=identout(i),
    )
    return identout(i)


@router.put("/identities/{identity_id}")
def save_identity(
    identity_id: uuid.UUID, data: schemas.IdentitySave, ctx: ProjectContext = EDIT, db: Session = DB
):
    i = identity(db, ctx, identity_id)
    db.refresh(i, with_for_update=True)
    if i.revision != data.revision:
        raise Conflict("角色身份已有新版本")
    asset(db, ctx, data.asset_id)
    before = identout(i)
    for k, v in data.model_dump(exclude={"revision"}).items():
        setattr(i, k, v)
    i.revision += 1
    audit.record(
        db,
        action="identity.update",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=i.id,
        detail={"before": before, "after": identout(i)},
    )
    return identout(i)


@router.post("/suggestions")
def suggestions(data: schemas.Suggest, ctx: ProjectContext = READ, db: Session = DB):
    result = indexing.search(
        db,
        ctx,
        MediaSearchIn(query=data.query, offset=data.offset, limit=8, collapse_versions=False),
    )
    return {
        **result,
        "notice": "以下只是外观或场景相似候选，不能据此认定身份。核对画面后再建立证据。",
    }


@router.get("")
def listing(
    identity_id: uuid.UUID | None = None,
    with_identity: uuid.UUID | None = None,
    unknown: bool = False,
    kind: Literal["visual", "speech"] | None = None,
    status: Literal["proposed", "confirmed", "rejected"] | None = None,
    offset: int = Query(0, ge=0),
    ctx: ProjectContext = READ,
    db: Session = DB,
):
    where = [IdentityEvidence.project_id == ctx.project.id, LibraryMedia.deleted_at.is_(None)]
    if identity_id:
        identity(db, ctx, identity_id)
        where.append(IdentityEvidence.identity_id == identity_id)
    if unknown:
        where.append(IdentityEvidence.identity_id.is_(None))
    if kind:
        where.append(IdentityEvidence.kind == kind)
    if status:
        where.append(IdentityEvidence.status == status)
    if with_identity:
        identity(db, ctx, with_identity)
        if not identity_id or with_identity == identity_id:
            raise CapabilityUnsupported("同框检索需要两个不同身份")
        other = aliased(IdentityEvidence)
        where.extend(
            [
                IdentityEvidence.kind == "visual",
                IdentityEvidence.status == "confirmed",
                select(other.id)
                .where(
                    other.project_id == ctx.project.id,
                    other.identity_id == with_identity,
                    other.kind == "visual",
                    other.status == "confirmed",
                    other.version_id == IdentityEvidence.version_id,
                    other.start_ms < IdentityEvidence.end_ms,
                    other.end_ms > IdentityEvidence.start_ms,
                )
                .exists(),
            ]
        )
    query = (
        select(IdentityEvidence, MediaVersion, LibraryMedia)
        .join(MediaVersion, IdentityEvidence.version_id == MediaVersion.id)
        .join(LibraryMedia, MediaVersion.media_id == LibraryMedia.id)
        .where(*where)
    )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "total": total,
        "items": [
            {
                **payload(e),
                "name": m.name,
                "ordinal": v.ordinal,
                "proxy_hash": v.proxy_hash,
                "poster_hash": v.poster_hash,
                "media_id": str(m.id),
            }
            for e, v, m in db.execute(
                query.order_by(IdentityEvidence.created_at.desc(), IdentityEvidence.id)
                .offset(offset)
                .limit(50)
            )
        ],
        "relation": "confirmed_visual_overlap" if with_identity else None,
    }


@router.post("")
def create(data: schemas.Create, ctx: ProjectContext = EDIT, db: Session = DB):
    _, v = library.version(db, ctx.project.id, data.version_id, lock=True)
    if v.status != "ready" or data.end_ms > (v.duration_ms or 0):
        raise CapabilityUnsupported("证据范围必须位于已处理的视频版本内")
    if data.identity_id:
        identity(db, ctx, data.identity_id)
    if not data.observation.strip():
        raise CapabilityUnsupported("请记录实际观察内容")
    e = IdentityEvidence(
        project_id=ctx.project.id,
        created_by=ctx.user.id,
        revision=0,
        source={"method": "human_annotation"},
        **data.model_dump(),
    )
    db.add(e)
    db.flush()
    remember(db, ctx, e)
    return payload(e)


@router.put("/{evidence_id}")
def correct(
    evidence_id: uuid.UUID, data: schemas.Save, ctx: ProjectContext = EDIT, db: Session = DB
):
    e = get(db, ctx, evidence_id)
    library.version(db, ctx.project.id, e.version_id, lock=True)
    db.refresh(e, with_for_update=True)
    if e.revision != data.revision:
        raise Conflict("证据已被其他人修正，请重新载入")
    if data.identity_id:
        identity(db, ctx, data.identity_id)
    if not data.observation.strip():
        raise CapabilityUnsupported("请记录实际观察内容")
    for k, v in data.model_dump(exclude={"revision"}).items():
        setattr(e, k, v)
    e.revision += 1
    remember(db, ctx, e)
    return payload(e)


@router.get("/{evidence_id}/history")
def history(
    evidence_id: uuid.UUID,
    offset: int = Query(0, ge=0),
    ctx: ProjectContext = READ,
    db: Session = DB,
):
    get(db, ctx, evidence_id)
    return [
        {"revision": r.revision, "document": r.document, "created_at": r.created_at}
        for r in db.scalars(
            select(EvidenceRevision)
            .where(EvidenceRevision.evidence_id == evidence_id)
            .order_by(EvidenceRevision.revision.desc())
            .offset(offset)
            .limit(50)
        )
    ]


@router.post("/{evidence_id}/history/{revision}/restore")
def restore(
    evidence_id: uuid.UUID,
    revision: int,
    data: schemas.Revision,
    ctx: ProjectContext = EDIT,
    db: Session = DB,
):
    get(db, ctx, evidence_id)
    r = db.scalar(
        select(EvidenceRevision).where(
            EvidenceRevision.evidence_id == evidence_id, EvidenceRevision.revision == revision
        )
    )
    if not r:
        raise NotFound("证据历史版本不存在")
    return correct(
        evidence_id,
        schemas.Save(
            revision=data.revision, **{k: r.document[k] for k in schemas.Correction.model_fields}
        ),
        ctx,
        db,
    )
