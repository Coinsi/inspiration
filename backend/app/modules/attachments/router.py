# FastAPI declares dependency injection in endpoint defaults.
# ruff: noqa: B008
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.models.conversation import Conversation, ConversationMessage
from app.modules.attachments import schemas, service

router = APIRouter(prefix="/projects/{project_id}/attachments", tags=["conversation-attachments"])
EDIT = require_action("agent.run")


@router.post("/uploads")
def begin(
    data: schemas.UploadIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.begin(db, ctx, data)


@router.get("")
def listing(
    query: str = Query(default="", max_length=255),
    kind: Literal["image", "video", "audio"] | None = None,
    favorite: bool | None = None,
    offset: int = Query(default=0, ge=0, le=1_000_000),
    limit: int = Query(default=24, ge=1, le=100),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.listing(
        db, ctx, query=query, kind=kind, favorite=favorite, offset=offset, limit=limit
    )


@router.get("/{attachment_id}")
@router.get("/{attachment_id}/manifest")
def detail(
    attachment_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    item = service.get(db, ctx, attachment_id)
    result = service.describe(item)
    result["message_ordinal"] = None
    if item.message_id and item.conversation_id:
        result["message_ordinal"] = db.scalar(
            select(ConversationMessage.ordinal)
            .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
            .where(
                ConversationMessage.id == item.message_id,
                Conversation.id == item.conversation_id,
                Conversation.project_id == ctx.project.id,
                Conversation.created_by == ctx.user.id,
            )
        )
    return result


@router.post("/{attachment_id}/chunk")
def chunk(
    attachment_id: uuid.UUID,
    offset: int = Query(..., ge=0),
    file: UploadFile = File(...),
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.append(db, ctx, attachment_id, offset, file)


@router.post("/{attachment_id}/complete")
def complete(
    attachment_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    result = service.complete(db, ctx, attachment_id)
    db.commit()
    if result["status"] == "ready":
        service.cleanup_staging(attachment_id)
    return result


@router.delete("/{attachment_id}")
def cancel(
    attachment_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    result = service.cancel(db, ctx, attachment_id)
    db.commit()
    service.cleanup_staging(attachment_id)
    return result


@router.patch("/{attachment_id}")
def update(
    attachment_id: uuid.UUID,
    data: schemas.UpdateIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.update(db, ctx, attachment_id, data)
