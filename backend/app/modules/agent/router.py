import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.models.agent import AgentRun
from app.modules.agent import schemas, service, tools

router = APIRouter(prefix="/projects/{project_id}/agent", tags=["agent"])
DB = Depends(get_db, scope="function")
READ = Depends(get_project_context)
RUN = Depends(require_action("agent.run"))


@router.get("/tools")
def catalogue(ctx: ProjectContext = READ):
    return tools.catalogue()


@router.post("/runs")
def create(data: schemas.Create, ctx: ProjectContext = RUN, db: Session = DB):
    return service.create(db, ctx, data)


@router.get("/runs")
def listing(ctx: ProjectContext = READ, db: Session = DB, offset: int = Query(0, ge=0)):
    return [
        service.out(r, False)
        for r in db.scalars(
            select(AgentRun)
            .where(AgentRun.project_id == ctx.project.id)
            .order_by(AgentRun.created_at.desc(), AgentRun.id)
            .offset(offset)
            .limit(30)
        )
    ]


@router.get("/runs/{run_id}")
def read(run_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB):
    return service.out(service.get_run(db, ctx, run_id))


@router.post("/runs/{run_id}/steps/{step_id}/decision")
def decision(
    run_id: uuid.UUID,
    step_id: str,
    data: schemas.Decision,
    ctx: ProjectContext = RUN,
    db: Session = DB,
):
    return service.review(db, ctx, run_id, step_id, data)


@router.post("/runs/{run_id}/control")
def control(run_id: uuid.UUID, data: schemas.Control, ctx: ProjectContext = RUN, db: Session = DB):
    return service.control(db, ctx, run_id, data)


@router.post("/tool-invocations")
def invoke(data: schemas.ToolInvocation, ctx: ProjectContext = READ, db: Session = DB):
    return service.invoke_external(db, ctx, data)
