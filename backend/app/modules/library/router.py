import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.modules.library import indexing, schemas, service

router = APIRouter(prefix="/projects/{project_id}/library", tags=["video-library"])
EDIT = require_action("asset.edit")
REFERENCE = require_action("shot.edit")
GENERATE = require_action("generation.trigger")


@router.get("/folders")
def folder_list(
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.library import folders

    return folders.listing(db, ctx.project.id)


@router.post("/folders")
def folder_create(
    data: schemas.FolderIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.library import folders

    return folders.save(db, ctx, data)


@router.put("/folders/{folder_id}")
def folder_save(
    folder_id: uuid.UUID,
    data: schemas.FolderSave,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.library import folders

    return folders.save(db, ctx, data, folder_id)


@router.delete("/folders/{folder_id}")
def folder_delete(
    folder_id: uuid.UUID,
    revision: int = Query(ge=1),
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.library import folders

    return folders.remove(db, ctx, folder_id, revision)


@router.post("/move")
def media_move(
    data: schemas.MediaMoveIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.library import folders

    return folders.move_media(db, ctx, data)


@router.get("/reuse-projects")
def reuse_projects(
    ctx: ProjectContext = Depends(EDIT), db: Session = Depends(get_db, scope="function")
):
    from app.modules.library import reuse

    return reuse.projects(db, ctx)


@router.post("/reuse")
def reuse_version(
    data: schemas.ReuseIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.library import reuse

    return reuse.create(db, ctx, data)


@router.get("/catalog")
def catalog(
    deleted: bool = False,
    query: str = Query(default="", max_length=255),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    limit: int = Query(default=24, ge=1, le=100),
    sort: Literal["recent", "oldest", "name"] = "recent",
    folder_id: uuid.UUID | None = None,
    root_only: bool = False,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.catalog(
        db, ctx.project.id, deleted, query, offset, limit, sort, folder_id, root_only
    )


@router.get("/version-catalog")
def version_catalog(
    query: str = Query(default="", max_length=255),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    limit: int = Query(default=24, ge=1, le=100),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.version_catalog(db, ctx.project.id, query, offset, limit)


@router.get("/items/{media_id}")
def media_detail(
    media_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    item = service.media(db, ctx.project.id, media_id, include_deleted=True)
    return service.media_entries(db, ctx.project.id, [item])[0]


@router.post("/usages/{usage_id}/materialize")
def materialize(
    usage_id: uuid.UUID,
    data: schemas.MaterializeIn,
    ctx: ProjectContext = Depends(GENERATE),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.generation.schemas import JobOut
    from app.modules.library import materialization

    return JobOut.model_validate(materialization.submit(db, ctx, usage_id, data))


@router.get("/index-status")
def index_status(
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return indexing.status(db, ctx.project.id)


@router.post("/search")
def search(
    data: schemas.MediaSearchIn,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return indexing.search(db, ctx, data)


@router.post("/versions/{version_id}/index")
def submit_index(
    version_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return indexing.submit(db, ctx, version_id)


@router.post("/indices/{index_id}/cancel")
def cancel_index(
    index_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return indexing.cancel(db, ctx, index_id)


@router.post("/versions/{version_id}/annotations")
def annotate(
    version_id: uuid.UUID,
    data: schemas.AnnotationIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return indexing.add_annotation(db, ctx, version_id, data)


@router.get("/versions/{version_id}/annotations")
def get_annotations(
    version_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return indexing.annotations(db, ctx.project.id, version_id, offset)


@router.delete("/annotations/{annotation_id}")
def unannotate(
    annotation_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    indexing.remove_annotation(db, ctx, annotation_id)
    return {"removed": True}


@router.post("/versions/{version_id}/subtitles")
def import_subtitles(
    version_id: uuid.UUID,
    data: schemas.SubtitleImportIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return indexing.import_subtitles(db, ctx, version_id, data.content)


@router.get("")
def listing(
    deleted: bool = False,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.list_media(db, ctx.project.id, deleted)


@router.post("/uploads")
def upload(
    data: schemas.UploadIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.begin_upload(db, ctx, data)


@router.get("/versions/{version_id}")
def version(
    version_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    item, v = service.version(db, ctx.project.id, version_id)
    return {**service.version_out(v), "name": item.name, "chunk_hashes": v.chunk_hashes}


@router.post("/versions/{version_id}/chunk")
def chunk(
    version_id: uuid.UUID,
    offset: int = Query(ge=0),
    file: UploadFile = File(...),
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.append_chunk(db, ctx, version_id, offset, file)


@router.post("/versions/{version_id}/complete")
def complete(
    version_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.complete_upload(db, ctx, version_id)


@router.post("/versions/{version_id}/retry")
def retry(
    version_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.retry(db, ctx, version_id)


@router.post("/versions/{version_id}/cancel")
def cancel(
    version_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    service.cancel_upload(db, ctx, version_id)
    return {"canceled": True}


@router.get("/usages")
def usages(
    media_id: uuid.UUID | None = None,
    shot_id: uuid.UUID | None = None,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.usages(db, ctx.project.id, media_id, shot_id)


@router.post("/usages")
def reference(
    data: schemas.UsageIn,
    ctx: ProjectContext = Depends(REFERENCE),
    db: Session = Depends(get_db, scope="function"),
):
    return service.create_usage(db, ctx, data)


@router.delete("/usages/{usage_id}")
def unreference(
    usage_id: uuid.UUID,
    ctx: ProjectContext = Depends(REFERENCE),
    db: Session = Depends(get_db, scope="function"),
):
    service.remove_usage(db, ctx, usage_id)
    return {"removed": True}


@router.get("/{media_id}/deletion-impact")
def impact(
    media_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.library import reuse

    service.media(db, ctx.project.id, media_id, include_deleted=True)
    return {
        **service.impact(db, ctx.project.id, media_id),
        "reuses": reuse.destinations(db, ctx.user.id, media_id),
    }


@router.post("/{media_id}/trash")
def trash(
    media_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.trash(db, ctx, media_id)


@router.post("/{media_id}/restore")
def restore(
    media_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.trash(db, ctx, media_id, restore=True)
