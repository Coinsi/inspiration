import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.core.errors import Conflict
from app.models.transcription import TranscriptionRun
from app.modules.media.subtitles import export_srt
from app.modules.transcription import schemas, service

router = APIRouter(prefix="/projects/{project_id}/transcriptions", tags=["transcription"])
DB = Depends(get_db, scope="function")
READ = Depends(get_project_context)
EDIT = Depends(require_action("asset.edit"))


@router.get("")
def listing(
    version_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0),
    ctx: ProjectContext = READ,
    db: Session = DB,
):
    q = select(TranscriptionRun).where(TranscriptionRun.project_id == ctx.project.id)
    if version_id:
        q = q.where(TranscriptionRun.version_id == version_id)
    return [
        service.out(r, False)
        for r in db.scalars(
            q.order_by(TranscriptionRun.created_at.desc(), TranscriptionRun.id)
            .offset(offset)
            .limit(50)
        )
    ]


@router.post("")
def start(data: schemas.Create, ctx: ProjectContext = EDIT, db: Session = DB):
    return service.start(db, ctx, data)


@router.get("/{run_id}")
def read(run_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB):
    return service.out(service.get(db, ctx, run_id))


@router.put("/{run_id}")
def save(run_id: uuid.UUID, data: schemas.Save, ctx: ProjectContext = EDIT, db: Session = DB):
    return service.save(db, ctx, run_id, data)


@router.post("/{run_id}/publish")
def publish(
    run_id: uuid.UUID, data: schemas.Revision, ctx: ProjectContext = EDIT, db: Session = DB
):
    return service.publish(db, ctx, run_id, data.revision)


@router.get("/{run_id}/srt")
def download(run_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB):
    r = service.get(db, ctx, run_id)
    if r.status != "ready":
        raise Conflict("转写完成后才能导出")
    return Response(
        export_srt(r.cues),
        media_type="application/x-subrip",
        headers={"Content-Disposition": 'attachment; filename="transcript.srt"'},
    )


@router.post("/{run_id}/control")
def control(run_id: uuid.UUID, data: schemas.Control, ctx: ProjectContext = EDIT, db: Session = DB):
    r = service.get(db, ctx, run_id, True)
    if data.action == "cancel" and r.status in ("queued", "processing"):
        r.cancel_requested = True
        if r.status == "queued":
            r.status = "canceled"
    elif data.action == "retry":
        if r.status not in ("failed", "canceled"):
            raise Conflict("仅失败或取消任务可续接")
        active = db.scalar(
            select(TranscriptionRun.id).where(
                TranscriptionRun.version_id == r.version_id,
                TranscriptionRun.status.in_(["queued", "processing"]),
                TranscriptionRun.id != r.id,
            )
        )
        if active:
            raise Conflict("这个视频已有转写任务正在执行")
        health = service.inference("/health")
        if health.get("model_key") != r.model_key:
            raise Conflict("识别模型已变化，请创建新的转写任务")
        r.status = "queued"
        r.error = None
        r.cancel_requested = False
    return service.out(r)
