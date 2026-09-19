"""assist 业务:会话管理 + 提议(对话→ops)+ 应用(落库+版本快照)。"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.adapters.contracts import assist_registry
from app.core import audit
from app.core.deps import ProjectContext
from app.core.errors import Conflict, NotFound
from app.models.assist import AssistChat
from app.modules.assist import schemas
from app.modules.assist.targets import OpError, get_adapter
from app.modules.narrative.service import _llm_config  # 复用项目 LLM 配置选择

# 确保引擎注册
import app.adapters.assist  # noqa: E402,F401


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _engine(db: Session, project_id: uuid.UUID, name: str | None):
    if name == "mock":
        return assist_registry.create("mock")
    pc = _llm_config(db, project_id)
    chosen = name or ("cloud_llm" if pc is not None else "mock")
    kwargs = {}
    if chosen == "cloud_llm" and pc is not None:
        from app.core.crypto import decrypt

        kwargs = {
            "base_url": pc.endpoint,
            "api_key": decrypt(pc.credentials_encrypted),
            "model": (pc.config or {}).get("model"),
        }
    return assist_registry.create(chosen, **kwargs)


def _get_chat(db: Session, project_id: uuid.UUID, chat_id: uuid.UUID) -> AssistChat:
    c = db.get(AssistChat, chat_id)
    if c is None or c.project_id != project_id:
        raise NotFound("会话不存在")
    return c


def create_chat(db: Session, ctx: ProjectContext, data: schemas.CreateChatIn) -> AssistChat:
    adapter = get_adapter(data.target_type)  # 校验类型受支持
    # 校验目标对象存在(并可读)
    adapter.read(db, ctx.project.id, data.target_id)
    c = AssistChat(
        project_id=ctx.project.id,
        target_type=data.target_type,
        target_id=data.target_id,
        title=data.title,
        messages=[],
        created_by=ctx.user.id,
    )
    db.add(c)
    db.flush()
    return c


def list_chats(
    db: Session, project_id: uuid.UUID,
    target_type: str | None = None, target_id: uuid.UUID | None = None,
) -> list[AssistChat]:
    stmt = select(AssistChat).where(AssistChat.project_id == project_id)
    if target_type:
        stmt = stmt.where(AssistChat.target_type == target_type)
    if target_id:
        stmt = stmt.where(AssistChat.target_id == target_id)
    return list(db.scalars(stmt.order_by(AssistChat.created_at.desc())))


def get_chat_state(db: Session, project_id: uuid.UUID, chat: AssistChat) -> dict:
    adapter = get_adapter(chat.target_type)
    _obj, state = adapter.read(db, project_id, chat.target_id)
    return state


def post_message(
    db: Session, ctx: ProjectContext, chat_id: uuid.UUID, data: schemas.PostMessageIn
) -> dict:
    """处理一条用户指令:产出助手回复 + 修改提议(含 dry-run 预览),但不立即应用。"""
    chat = _get_chat(db, ctx.project.id, chat_id)
    adapter = get_adapter(chat.target_type)
    _obj, state = adapter.read(db, ctx.project.id, chat.target_id)

    # S3:剧本对象注入设定库上下文,AI 改稿遵守既定人设/世界观
    if chat.target_type == "script":
        from app.models.narrative import Chapter, Script
        from app.modules.setting.service import settings_context

        s = db.get(Script, chat.target_id)
        ch = db.get(Chapter, s.source_chapter_id) if s is not None and s.source_chapter_id else None
        if ch is not None:
            ctx_text = settings_context(db, ctx.project.id, ch.novel_id, ch.ordinal, max_chars=2500)
            if ctx_text:
                state = {**state, "story_settings(已确立的故事设定,修改时必须遵守)": ctx_text}

    from app.modules.skill.service import compose
    from app.modules.identity.preferences import guide
    instruction, skill_refs = compose(db, ctx.project.id, data.skills, guide(db, ctx.project.id, data.content, "assistant"))
    engine = _engine(db, ctx.project.id, data.engine)
    proposal = engine.propose(
        object_desc=adapter.object_desc,
        ops_help=adapter.ops_help,
        state=state,
        history=chat.messages or [],
        instruction=instruction,
    )

    # dry-run 预览:提议可应用才给 preview;不可应用则转为澄清,避免脏提议
    preview = None
    ops = proposal.ops if not proposal.needs_clarification else []
    reply = proposal.reply
    if ops:
        try:
            preview = adapter.apply(db, ctx, chat.target_id, ops, dry_run=True)["state"]
        except OpError as e:
            reply = (reply + f"(无法自动应用:{e})").strip()
            ops = []
            proposal.needs_clarification = True

    user_msg = {"id": uuid.uuid4().hex, "role": "user", "content": data.content, "created_at": _now(), "skills": skill_refs}
    asst_msg = {
        "id": uuid.uuid4().hex,
        "role": "assistant",
        "content": reply,
        "created_at": _now(),
        "proposal": {
            "summary": proposal.summary,
            "ops": ops,
            "needs_clarification": proposal.needs_clarification,
            "preview": preview,
            "applied_at": None,
        },
    }
    chat.messages = list(chat.messages or []) + [user_msg, asst_msg]
    flag_modified(chat, "messages")  # 确保 JSONB 变更被持久化
    audit.record(db, action="assist.message", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=chat.target_type, target_id=chat.target_id,
                 detail={"chat_id": str(chat.id), "engine": engine.name, "ops": len(ops)})
    db.flush()
    return {"chat_id": chat.id, "message": asst_msg}


def apply_message(
    db: Session, ctx: ProjectContext, chat_id: uuid.UUID, message_id: str
) -> dict:
    """应用某条助手消息里的修改提议:落库 + 版本快照,标记已应用。"""
    chat = _get_chat(db, ctx.project.id, chat_id)
    msgs = list(chat.messages or [])
    target = next((m for m in msgs if m.get("id") == message_id and m.get("role") == "assistant"), None)
    if target is None:
        raise NotFound("消息不存在")
    proposal = target.get("proposal") or {}
    if proposal.get("applied_at"):
        raise Conflict("该修改已应用过,请勿重复应用")
    ops = proposal.get("ops") or []
    if not ops:
        raise Conflict("这条消息没有可应用的修改")

    adapter = get_adapter(chat.target_type)
    # 按目标对象类型校验写权限(script→narrative.edit / shot→shot.edit / asset→asset.edit …)
    from app.core.permissions import require

    require(ctx.role, adapter.edit_action)
    try:
        result = adapter.apply(db, ctx, chat.target_id, ops, dry_run=False)
    except OpError as e:
        raise Conflict(f"应用失败:{e}") from e

    proposal["applied_at"] = _now()
    proposal["version_id"] = result.get("version_id")
    target["proposal"] = proposal
    chat.messages = msgs
    flag_modified(chat, "messages")  # 确保 JSONB 变更被持久化
    audit.record(db, action="assist.apply", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=chat.target_type, target_id=chat.target_id,
                 detail={"chat_id": str(chat.id), "message_id": message_id, "version_id": result.get("version_id")})
    db.flush()
    return {"applied": True, "state": result.get("state"), "version_id": result.get("version_id")}
