import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, _bearer, get_project_context, require_action
from app.models.conversation import Conversation
from app.modules.conversation import schemas, service

router = APIRouter(prefix="/projects/{project_id}/conversations", tags=["conversations"])
DB = Depends(get_db, scope="function")
READ = Depends(get_project_context)
WRITE = Depends(require_action("agent.run"))


@router.get("/{conversation_id}/events")
def live_events(
    conversation_id: uuid.UUID,
    request: Request,
    ctx: ProjectContext = READ,
    db: Session = DB,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
):
    from app.modules.conversation.live import events

    service.get(db, ctx, conversation_id)
    return StreamingResponse(
        events(request, credentials, ctx.project.id, conversation_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


@router.get("")
def listing(
    ctx: ProjectContext = READ,
    db: Session = DB,
    archived: bool = False,
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=120),
):
    query = select(Conversation).where(
        Conversation.project_id == ctx.project.id,
        Conversation.created_by == ctx.user.id,
        Conversation.archived_at.is_not(None) if archived else Conversation.archived_at.is_(None),
    )
    if q.strip():
        query = query.where(Conversation.title.ilike(f"%{q.strip()}%"))
    return [
        service.summary(c)
        for c in db.scalars(
            query.order_by(Conversation.updated_at.desc(), Conversation.id).offset(offset).limit(30)
        )
    ]


@router.post("")
def create(data: schemas.Create, ctx: ProjectContext = WRITE, db: Session = DB):
    return service.create(db, ctx, data)


@router.get("/{conversation_id}")
def read(
    conversation_id: uuid.UUID,
    ctx: ProjectContext = READ,
    db: Session = DB,
    before: int | None = Query(None, ge=1),
):
    return service.detail(db, ctx, conversation_id, before)


@router.patch("/{conversation_id}")
def update(
    conversation_id: uuid.UUID, data: schemas.Update, ctx: ProjectContext = WRITE, db: Session = DB
):
    return service.update(db, ctx, conversation_id, data)


@router.post("/{conversation_id}/messages")
def send(
    conversation_id: uuid.UUID, data: schemas.Send, ctx: ProjectContext = WRITE, db: Session = DB
):
    return service.send(db, ctx, conversation_id, data)


@router.post("/{conversation_id}/messages/{message_id}/control")
def control(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    data: schemas.Control,
    ctx: ProjectContext = WRITE,
    db: Session = DB,
):
    return service.control(db, ctx, conversation_id, message_id, data)


@router.get("/{conversation_id}/messages/{message_id}/retry-input")
def retry_input(
    conversation_id: uuid.UUID, message_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB
):
    return service.retry_input(db, ctx, conversation_id, message_id, allow_completed=True)


@router.post("/{conversation_id}/messages/{message_id}/retry")
def retry(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    data: schemas.Retry,
    ctx: ProjectContext = WRITE,
    db: Session = DB,
):
    return service.retry(db, ctx, conversation_id, message_id, data)
