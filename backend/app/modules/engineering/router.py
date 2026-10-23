import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy import select

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context
from app.models.engineering import EngineeringChange, EngineeringCommit
from app.modules.engineering import schemas, service

router = APIRouter(
    prefix="/projects/{project_id}/works/{work_id}/engineering", tags=["engineering"]
)
DB = Depends(get_db, scope="function")
CTX = Depends(get_project_context)


@router.post("/archive-uploads")
def create_archive_upload(
    work_id: uuid.UUID,
    request_key: uuid.UUID,
    size: int = Query(gt=0),
    filename: str = Query(min_length=1, max_length=255),
    ctx: ProjectContext = CTX,
    db=DB,
):
    from app.modules.engineering import archive_uploads

    return archive_uploads.create(db, ctx, work_id, request_key, filename, size)


@router.put("/archive-uploads/{job_id}")
async def append_archive_upload(
    work_id: uuid.UUID,
    job_id: uuid.UUID,
    request: Request,
    offset: int = Query(ge=0),
    ctx: ProjectContext = CTX,
    db=DB,
):
    from starlette.concurrency import run_in_threadpool

    from app.core.errors import Conflict
    from app.modules.engineering import archive_uploads

    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > archive_uploads.CHUNK_BYTES:
            raise Conflict("上传分片超过 8 MiB")
        data.extend(chunk)
    return await run_in_threadpool(
        archive_uploads.append, db, ctx, work_id, job_id, offset, bytes(data)
    )


@router.post("/archive-uploads/{job_id}/complete")
def complete_archive_upload(
    work_id: uuid.UUID, job_id: uuid.UUID, ctx: ProjectContext = CTX, db=DB
):
    from app.modules.engineering import archive_uploads

    return archive_uploads.finish(db, ctx, work_id, job_id)


@router.post("/archive-jobs")
def create_archive_job(
    work_id: uuid.UUID,
    request_key: uuid.UUID,
    revision: int | None = Query(None, ge=0),
    ctx: ProjectContext = CTX,
    db=DB,
):
    from app.modules.engineering import archive_jobs

    return archive_jobs.create(db, ctx, work_id, request_key, revision)


@router.get("/archive-jobs")
def list_archive_jobs(work_id: uuid.UUID, ctx: ProjectContext = CTX, db=DB):
    from app.models.engineering import EngineeringArchiveJob
    from app.modules.engineering import archive_jobs

    service.work(db, ctx, work_id)
    rows = db.scalars(
        select(EngineeringArchiveJob)
        .where(
            EngineeringArchiveJob.work_id == work_id,
        )
        .order_by(EngineeringArchiveJob.created_at.desc())
        .limit(30)
    )
    return [archive_jobs.output(row) for row in rows]


@router.post("/archive-jobs/{job_id}/{operation}")
def archive_action(
    work_id: uuid.UUID,
    job_id: uuid.UUID,
    operation: str,
    ctx: ProjectContext = CTX,
    db=DB,
):
    from app.modules.engineering import archive_jobs

    return archive_jobs.action(db, ctx, work_id, job_id, operation)


@router.get("/archive-jobs/{job_id}/download")
def archive_download(work_id: uuid.UUID, job_id: uuid.UUID, ctx: ProjectContext = CTX, db=DB):
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import FileResponse

    from app.core.errors import Conflict
    from app.modules.engineering import archive_jobs

    row = archive_jobs.get(db, ctx, work_id, job_id)
    service.validate_sources(db, ctx, work_id, row.snapshot)
    guard = Session(bind=db.get_bind())
    try:
        key = int.from_bytes(job_id.bytes[:8], "big", signed=True)
        guard.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": key})
        db.refresh(row)
        path = archive_jobs.directory(job_id) / "result.zip"
        if row.operation != "export" or row.status != "succeeded" or not path.is_file():
            raise Conflict("归档文件尚不可下载")
    except BaseException:
        guard.close()
        raise

    class GuardedFileResponse(FileResponse):
        async def __call__(self, scope, receive, send):
            try:
                await super().__call__(scope, receive, send)
            finally:
                await run_in_threadpool(guard.close)

    return GuardedFileResponse(
        path, media_type="application/zip", filename=f"{work_id}-r{row.revision}.inspiration.zip"
    )


@router.get("")
def read(work_id: uuid.UUID, ctx: ProjectContext = CTX, db=DB):
    return service.read(db, ctx, work_id)


@router.post("/locate-time")
def locate_time(work_id: uuid.UUID, data: schemas.LocateTime, ctx: ProjectContext = CTX, db=DB):
    from app.modules.engineering.locate import locate

    service.work(db, ctx, work_id)
    return locate(db, work_id, data)


@router.get("/history")
def history(work_id: uuid.UUID, ctx: ProjectContext = CTX, db=DB, offset: int = Query(0, ge=0)):
    service.work(db, ctx, work_id)
    return list(
        db.scalars(
            select(EngineeringCommit)
            .where(EngineeringCommit.work_id == work_id)
            .order_by(EngineeringCommit.revision.desc())
            .offset(offset)
            .limit(30)
        )
    )


@router.get("/changes")
def changes(work_id: uuid.UUID, ctx: ProjectContext = CTX, db=DB, offset: int = Query(0, ge=0)):
    service.work(db, ctx, work_id)
    return [
        service.change_out(c)
        for c in db.scalars(
            select(EngineeringChange)
            .where(EngineeringChange.work_id == work_id)
            .order_by(EngineeringChange.created_at.desc(), EngineeringChange.id)
            .offset(offset)
            .limit(30)
        )
    ]


@router.post("/changes")
def propose(work_id: uuid.UUID, data: schemas.Propose, ctx: ProjectContext = CTX, db=DB):
    return service.propose(db, ctx, work_id, data)


@router.get("/changes/{change_id}")
def change(work_id: uuid.UUID, change_id: uuid.UUID, ctx: ProjectContext = CTX, db=DB):
    from app.modules.engineering import review

    service.work(db, ctx, work_id)
    row = review.get_change(db, work_id, change_id)
    return review.details(db, ctx, row)


@router.post("/changes/{change_id}/rebase")
def rebase(
    work_id: uuid.UUID, change_id: uuid.UUID, data: schemas.Rebase, ctx: ProjectContext = CTX, db=DB
):
    return service.rebase(db, ctx, work_id, change_id, data)


@router.post("/changes/{change_id}/accept")
def accept(
    work_id: uuid.UUID, change_id: uuid.UUID, data: schemas.Accept, ctx: ProjectContext = CTX, db=DB
):
    return service.accept(db, ctx, work_id, change_id, data)


@router.post("/changes/{change_id}/preview")
def preview(
    work_id: uuid.UUID,
    change_id: uuid.UUID,
    data: schemas.Preview,
    ctx: ProjectContext = CTX,
    db=DB,
):
    from app.modules.engineering import review

    return review.preview(db, ctx, work_id, change_id, data)


@router.get("/documents")
def documents(work_id: uuid.UUID, ctx: ProjectContext = CTX, db=DB):
    from app.modules.engineering import documents as docs
    from app.modules.engineering import executors

    service.work(db, ctx, work_id)
    return {
        "format": 1,
        "supported_types": docs.SUPPORTED,
        "type_capabilities": docs.document_types.describe(),
        "type_contracts": docs.document_types.contracts(),
        "preview_executors": executors.describe(),
        "documents": docs.index(db, work_id),
    }


@router.get("/documents/{document_id}/revisions/{revision}")
def document_revision(
    work_id: uuid.UUID, document_id: uuid.UUID, revision: int, ctx: ProjectContext = CTX, db=DB
):
    from app.modules.engineering import documents as docs

    service.work(db, ctx, work_id)
    view = docs.sequence_view(db, work_id, document_id, revision)
    from app.models.generation import Generation, GenerationJob

    # A preview belongs to an exact document revision, never to a mutable title
    # or the newest job in the work. Restored historical roots keep their preview.
    results = db.scalars(
        select(Generation)
        .join(GenerationJob, GenerationJob.id == Generation.job_id)
        .where(
            Generation.project_id == ctx.project.id,
            GenerationJob.status == "succeeded",
            GenerationJob.input_snapshot["engineering_work_id"].astext == str(work_id),
            Generation.input_refs["engineering_plan"]["root"]["id"].astext == str(document_id),
            Generation.input_refs["engineering_plan"]["root"]["revision"].as_integer() == revision,
        )
        .order_by(Generation.created_at.desc(), Generation.id)
        .limit(3)
    )
    view["artifacts"] = [
        {
            "id": str(row.id),
            "blob_hash": row.output_blob_hash,
            "output_type": row.output_type,
            "executor": row.input_refs["engineering_plan"]["executor"],
        }
        for row in results
    ]
    return view


@router.get("/manifest")
def manifest(
    work_id: uuid.UUID, revision: int | None = Query(None, ge=0), ctx: ProjectContext = CTX, db=DB
):
    from app.modules.engineering import documents as docs

    work = service.work(db, ctx, work_id)
    number = work.engineering_revision if revision is None else revision
    snapshot = service.snapshot_at(db, work_id, number)
    ref = snapshot.get("manifest_ref")
    return {
        "format": "studio.engineering-manifest@1",
        "work_id": str(work_id),
        "revision": number,
        "manifest_ref": ref,
        "documents": docs.closure(db, work_id, ref) if ref else [],
        "includes_media_files": False,
    }


@router.get("/changes/{change_id}/impact")
def change_impact(work_id: uuid.UUID, change_id: uuid.UUID, ctx: ProjectContext = CTX, db=DB):
    from app.modules.engineering import impact, review

    service.work(db, ctx, work_id)
    change = review.get_change(db, work_id, change_id)
    before = service.snapshot_at(db, work_id, change.base_revision)
    return impact.compare(
        db,
        work_id,
        before.get("manifest_ref"),
        change.snapshot.get("manifest_ref"),
        change.snapshot.get("derived_documents", []),
    )


@router.get("/package")
def export_package(
    work_id: uuid.UUID, revision: int | None = Query(None, ge=0), ctx: ProjectContext = CTX, db=DB
):
    from app.modules.engineering import packages

    work = service.work(db, ctx, work_id)
    snapshot = service.snapshot_at(
        db, work_id, work.engineering_revision if revision is None else revision
    )
    service.validate_sources(db, ctx, work_id, snapshot)
    return packages.export(db, work_id, snapshot)


@router.get("/archive")
def export_archive(
    work_id: uuid.UUID, revision: int | None = Query(None, ge=0), ctx: ProjectContext = CTX, db=DB
):
    from sqlalchemy.orm import sessionmaker

    from app.modules.engineering import archive_jobs

    job = archive_jobs.create(db, ctx, work_id, uuid.uuid4(), revision)
    job_id = uuid.UUID(job["id"])
    db.commit()
    archive_jobs.execute(job_id, sessions=sessionmaker(bind=db.get_bind()))
    db.expire_all()
    return archive_download(work_id, job_id, ctx, db)


@router.post("/archive")
def import_archive(
    work_id: uuid.UUID,
    request_key: Annotated[uuid.UUID, Form()],
    file: Annotated[UploadFile, File()],
    ctx: ProjectContext = CTX,
    db=DB,
):
    from sqlalchemy.orm import sessionmaker

    from app.core.errors import Conflict
    from app.modules.engineering import archive_jobs, archive_uploads, review

    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    job = archive_uploads.create(db, ctx, work_id, request_key, file.filename or "work.zip", size)
    job_id = uuid.UUID(job["id"])
    db.commit()
    if job["status"] == "uploading":
        # Replay from zero verifies every previously acknowledged chunk as well.
        offset = 0
        while data := file.file.read(archive_uploads.CHUNK_BYTES):
            archive_uploads.append(db, ctx, work_id, job_id, offset, data)
            db.commit()
            offset += len(data)
        archive_uploads.finish(db, ctx, work_id, job_id)
        db.commit()
    archive_jobs.execute(job_id, sessions=sessionmaker(bind=db.get_bind()))
    db.expire_all()
    row = archive_jobs.get(db, ctx, work_id, job_id)
    if row.status != "succeeded":
        raise Conflict("工程归档尚未恢复，请在工程任务中查看进度或重试")
    return service.change_out(review.get_change(db, work_id, uuid.UUID(row.progress["change_id"])))
