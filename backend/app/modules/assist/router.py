"""assist 路由:贯穿式 AI 对话助手(可对任意受支持对象多轮对话改稿)。"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context
from app.modules.assist import schemas, service
from app.modules.assist.targets import supported_types

router = APIRouter(prefix="/projects/{project_id}/assist", tags=["assist"])


def _chat_out(chat) -> schemas.ChatOut:
    return schemas.ChatOut.model_validate(chat)


@router.get("/targets", response_model=list[str])
def list_supported_targets(ctx: ProjectContext = Depends(get_project_context)):
    """列出当前支持被对话修改的对象类型。"""
    return supported_types()


@router.post("/chats", response_model=schemas.ChatDetailOut)
def create_chat(
    data: schemas.CreateChatIn, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db, scope="function")
):
    chat = service.create_chat(db, ctx, data)
    out = schemas.ChatDetailOut.model_validate(chat)
    out.state = service.get_chat_state(db, ctx.project.id, chat)
    return out


@router.get("/chats", response_model=list[schemas.ChatOut])
def list_chats(
    target_type: str | None = Query(None),
    target_id: uuid.UUID | None = Query(None),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return [_chat_out(c) for c in service.list_chats(db, ctx.project.id, target_type, target_id)]


@router.get("/chats/{chat_id}", response_model=schemas.ChatDetailOut)
def get_chat(
    chat_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db, scope="function")
):
    chat = service._get_chat(db, ctx.project.id, chat_id)
    out = schemas.ChatDetailOut.model_validate(chat)
    out.state = service.get_chat_state(db, ctx.project.id, chat)
    return out


@router.post("/chats/{chat_id}/messages", response_model=schemas.SendMessageOut)
def post_message(
    chat_id: uuid.UUID, data: schemas.PostMessageIn,
    ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db, scope="function"),
):
    return service.post_message(db, ctx, chat_id, data)


@router.post("/chats/{chat_id}/messages/{message_id}/apply", response_model=schemas.ApplyOut)
def apply_message(
    chat_id: uuid.UUID, message_id: str,
    ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db, scope="function"),
):
    return service.apply_message(db, ctx, chat_id, message_id)
