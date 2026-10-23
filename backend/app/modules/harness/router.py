import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.models.harness import ProductionEvent, ProductionRun
from app.modules.harness import schemas, service
from app.modules.harness.kernel_control import SwitchKernelRequest, switch

router = APIRouter(prefix="/projects/{project_id}/production", tags=["production"])
DB = Depends(get_db, scope="function")
READ = Depends(get_project_context)
RUN = Depends(require_action("agent.run"))


class GuidanceConfirmation(schemas.Strict):
    request_key: uuid.UUID
    expected_revision: int


class GuidanceSubmission(GuidanceConfirmation):
    text: str
    task_ids: list[uuid.UUID] = []
    references: list[dict] = []


@router.post("/runs")
def create(data: schemas.Create, ctx: ProjectContext = RUN, db: Session = DB):
    return service.create(db, ctx, data)


@router.get("/runs")
def listing(ctx: ProjectContext = READ, db: Session = DB, offset: int = Query(0, ge=0)):
    rows = db.scalars(
        select(ProductionRun)
        .where(ProductionRun.project_id == ctx.project.id, ProductionRun.created_by == ctx.user.id)
        .order_by(ProductionRun.created_at.desc())
        .offset(offset)
        .limit(30)
    )
    return [service.out(db, run, False) for run in rows]


@router.get("/runs/{run_id}")
def read(run_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB):
    return service.out(db, service.get_run(db, ctx, run_id))


@router.get("/runs/{run_id}/events")
def events(
    run_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB, after: int = Query(0, ge=0)
):
    service.get_run(db, ctx, run_id)
    rows = db.scalars(
        select(ProductionEvent)
        .where(ProductionEvent.run_id == run_id, ProductionEvent.sequence > after)
        .order_by(ProductionEvent.sequence)
        .limit(100)
    )
    return [
        {
            "id": row.id,
            "sequence": row.sequence,
            "kind": row.kind,
            "payload": row.payload,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/runs/{run_id}/control")
def control(run_id: uuid.UUID, data: schemas.Control, ctx: ProjectContext = RUN, db: Session = DB):
    return service.control(db, ctx, run_id, data)


@router.post("/runs/{run_id}/kernel")
def switch_kernel(
    run_id: uuid.UUID, data: SwitchKernelRequest, ctx: ProjectContext = RUN, db: Session = DB
):
    return switch(db, ctx, run_id, data)


@router.post("/runs/{run_id}/guidance/{guidance_id}/confirm")
def confirm_guidance(
    run_id: uuid.UUID,
    guidance_id: uuid.UUID,
    data: GuidanceConfirmation,
    ctx: ProjectContext = RUN,
    db: Session = DB,
):
    from app.modules.harness.guidance import confirm

    return confirm(db, ctx, run_id, guidance_id, data.request_key, data.expected_revision)


@router.post("/runs/{run_id}/guidance")
def submit_guidance(
    run_id: uuid.UUID, data: GuidanceSubmission, ctx: ProjectContext = RUN, db: Session = DB
):
    from app.modules.harness.guidance import receive

    # User text is stored even if the graph progressed while the form was open.
    # Fixed task/reference anchors are revalidated by receive, never guessed.
    receive(
        db,
        ctx,
        run_id,
        data.request_key,
        data.text,
        {"task_ids": data.task_ids, "references": data.references},
    )
    return service.out(db, service.get_run(db, ctx, run_id))
