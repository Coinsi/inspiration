"""timeline 路由(对照 04 §3.8 / §3.7 基线)。"""

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.modules.timeline import schemas, service

router = APIRouter(prefix="/projects/{project_id}", tags=["timeline"])
EDIT = require_action("timeline.edit")
LOCK = require_action("lock.baseline")


@router.post("/timelines", response_model=schemas.TimelineOut)
def create_timeline(
    data: schemas.TimelineIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.create_timeline(db, ctx, data)


@router.get("/timelines", response_model=list[schemas.TimelineOut])
def list_timelines(
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.list_timelines(db, ctx.project.id)


@router.put("/timelines/{timeline_id}/items", response_model=list[schemas.TimelineItemOut])
def set_items(
    timeline_id: uuid.UUID,
    data: schemas.SetItemsIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.set_items(db, ctx, timeline_id, data.items, data.revision)


@router.get("/timelines/{timeline_id}/document")
def document(
    timeline_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.timeline import editing

    return editing.document(db, ctx.project.id, timeline_id)


@router.put("/timelines/{timeline_id}/document")
def save_document(
    timeline_id: uuid.UUID,
    data: schemas.TimelineDocumentIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.timeline import editing

    return editing.save_document(db, ctx, timeline_id, data)


@router.get("/timelines/{timeline_id}/history")
def history(
    timeline_id: uuid.UUID,
    offset: int = Query(default=0, ge=0, le=1_000_000),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.timeline import editing

    return editing.history(db, ctx, timeline_id, offset)


@router.post("/timelines/{timeline_id}/history/{revision}/restore")
def restore(
    timeline_id: uuid.UUID,
    revision: int,
    data: schemas.RestoreIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.timeline import editing

    return editing.restore(db, ctx, timeline_id, revision, data.revision)


@router.post("/timelines/{timeline_id}/subtitles/parse")
def parse_subtitles(
    timeline_id: uuid.UUID,
    data: schemas.SubtitleParseIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    from app.core.errors import CapabilityUnsupported
    from app.modules.media.subtitles import parse_srt

    service._get_timeline(db, ctx.project.id, timeline_id)
    try:
        return {"items": parse_srt(data.content, 1_800_000)}
    except ValueError as exc:
        raise CapabilityUnsupported(str(exc)) from exc


@router.post("/timelines/{timeline_id}/subtitles/from-sources")
def source_subtitles(
    timeline_id: uuid.UUID,
    data: schemas.SetItemsIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.timeline import captions

    return captions.preview(db, ctx, timeline_id, data.items)


@router.get("/timelines/{timeline_id}/subtitles.srt")
def export_subtitles(
    timeline_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.media.subtitles import export_srt

    t = service._get_timeline(db, ctx.project.id, timeline_id)
    return Response(
        export_srt(t.subtitles),
        media_type="application/x-subrip",
        headers={"Content-Disposition": 'attachment; filename="subtitles.srt"'},
    )


@router.post("/timelines/{timeline_id}/audio")
def upload_audio(
    timeline_id: uuid.UUID,
    file: UploadFile = File(...),
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.timeline import audio

    return audio.upload(db, ctx, timeline_id, file)


@router.get("/audio-sources")
def audio_sources(
    offset: int = Query(default=0, ge=0, le=1_000_000),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    from app.modules.timeline import audio

    return audio.sources(db, ctx, offset)


@router.get("/timelines/{timeline_id}/items", response_model=list[schemas.TimelineItemOut])
def list_items(
    timeline_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.list_items(db, ctx.project.id, timeline_id)


@router.post("/cuts", response_model=schemas.CutOut)
def create_cut(
    data: schemas.CutIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.create_cut(db, ctx, data)


@router.post("/cuts/{cut_id}/finalize", response_model=schemas.BaselineOut)
def finalize_cut(
    cut_id: uuid.UUID,
    ctx: ProjectContext = Depends(LOCK),
    db: Session = Depends(get_db, scope="function"),
):
    return service.finalize_cut(db, ctx, cut_id)


@router.get("/baselines", response_model=list[schemas.BaselineOut])
def list_baselines(
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.list_baselines(db, ctx.project.id)


@router.get("/baselines/compare")
def compare_baselines(
    a: uuid.UUID = Query(...),
    b: uuid.UUID = Query(...),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.compare_baselines(db, ctx.project.id, a, b)
