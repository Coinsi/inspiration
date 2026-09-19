import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.core.errors import NotFound
from app.models.canvas import Canvas, CanvasRevision, CanvasRun
from app.modules.canvas import schemas, service

router = APIRouter(prefix="/projects/{project_id}/canvases", tags=["canvas"])
DB = Depends(get_db, scope="function")
READ = Depends(get_project_context)
EDIT = Depends(require_action("canvas.edit"))
RUN = Depends(require_action("generation.trigger"))


@router.get("")
def listing(ctx: ProjectContext = READ, db: Session = DB, offset: int = Query(0, ge=0)):
    return [
        {"id": c.id, "name": c.name, "revision": c.revision}
        for c in db.scalars(
            select(Canvas)
            .where(Canvas.project_id == ctx.project.id)
            .order_by(Canvas.created_at.desc(), Canvas.id)
            .offset(offset)
            .limit(50)
        )
    ]


@router.post("")
def create(data: schemas.Create, ctx: ProjectContext = EDIT, db: Session = DB):
    c = Canvas(
        project_id=ctx.project.id,
        name=data.name,
        revision=0,
        document=schemas.Document().model_dump(),
        created_by=ctx.user.id,
    )
    db.add(c)
    db.flush()
    db.add(
        CanvasRevision(
            canvas_id=c.id,
            revision=0,
            document={"name": c.name, **c.document},
            created_by=ctx.user.id,
        )
    )
    return service.out(c)


@router.get("/{canvas_id}")
def read(canvas_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB):
    return service.out(service.get_canvas(db, ctx.project.id, canvas_id))


@router.put("/{canvas_id}")
def save(canvas_id: uuid.UUID, data: schemas.Save, ctx: ProjectContext = EDIT, db: Session = DB):
    return service.save(db, ctx, canvas_id, data)


@router.get("/{canvas_id}/history")
def history(
    canvas_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB, offset: int = Query(0, ge=0)
):
    service.get_canvas(db, ctx.project.id, canvas_id)
    return [
        {"revision": r.revision, "created_at": r.created_at}
        for r in db.scalars(
            select(CanvasRevision)
            .where(CanvasRevision.canvas_id == canvas_id)
            .order_by(CanvasRevision.revision.desc())
            .offset(offset)
            .limit(50)
        )
    ]


@router.post("/{canvas_id}/history/{revision}/restore")
def restore(
    canvas_id: uuid.UUID,
    revision: int,
    data: schemas.RevisionIn,
    ctx: ProjectContext = EDIT,
    db: Session = DB,
):
    service.get_canvas(db, ctx.project.id, canvas_id, True)
    old = db.scalar(
        select(CanvasRevision).where(
            CanvasRevision.canvas_id == canvas_id, CanvasRevision.revision == revision
        )
    )
    if not old:
        raise NotFound("历史版本不存在")
    return service.save(
        db,
        ctx,
        canvas_id,
        schemas.Save(
            name=old.document["name"],
            revision=data.revision,
            document=schemas.Document.model_validate(old.document),
        ),
    )


@router.post("/{canvas_id}/runs")
def start_run(
    canvas_id: uuid.UUID, data: schemas.StartRun, ctx: ProjectContext = RUN, db: Session = DB
):
    return service.start_run(db, ctx, canvas_id, data)


@router.get("/{canvas_id}/runs")
def runs(
    canvas_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB, offset: int = Query(0, ge=0)
):
    service.get_canvas(db, ctx.project.id, canvas_id)
    return [
        service.run_out(r)
        for r in db.scalars(
            select(CanvasRun)
            .where(CanvasRun.canvas_id == canvas_id)
            .order_by(CanvasRun.created_at.desc(), CanvasRun.id)
            .offset(offset)
            .limit(20)
        )
    ]


@router.post("/{canvas_id}/runs/{run_id}/control")
def control(
    canvas_id: uuid.UUID,
    run_id: uuid.UUID,
    data: schemas.Control,
    ctx: ProjectContext = RUN,
    db: Session = DB,
):
    return service.control(db, ctx, canvas_id, run_id, data.action)


@router.post("/{canvas_id}/import")
def import_media(
    canvas_id: uuid.UUID,
    file: UploadFile = File(...),
    request_key: uuid.UUID = Form(...),
    source_generation_id: uuid.UUID | None = Form(None),
    ctx: ProjectContext = EDIT,
    db: Session = DB,
):
    from app.modules.canvas.tools import import_file
    from app.modules.generation.schemas import GenerationOut

    return GenerationOut.model_validate(
        import_file(db, ctx, canvas_id, request_key, file, source_generation_id)
    )


@router.get("/{canvas_id}/export")
def export(canvas_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB):
    from app.modules.canvas.tools import bundle

    filename = bundle(db, ctx, canvas_id)
    return FileResponse(
        filename,
        media_type="application/zip",
        filename="canvas-workspace.zip",
        background=BackgroundTask(Path(filename).unlink, missing_ok=True),
    )


@router.post("/{canvas_id}/validate")
def validate_document(
    canvas_id: uuid.UUID, data: schemas.Document, ctx: ProjectContext = READ, db: Session = DB
):
    service.get_canvas(db, ctx.project.id, canvas_id)
    return service.validate(db, ctx.project.id, data.model_dump(mode="json"))
