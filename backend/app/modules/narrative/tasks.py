"""Database-backed adaptation queue; never resend an interrupted model request."""

import logging
import threading
from datetime import UTC, datetime, timedelta
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.deps import ProjectContext
from app.core.errors import AppError, Forbidden
from app.core.permissions import require
from app.models.identity import Project, User, Membership
from app.models.narrative import Chapter, ScriptGenerationTask
from app.modules.narrative import schemas, service

log = logging.getLogger(__name__)


def latest(db, project_id, chapter_id):
    return db.scalar(
        select(ScriptGenerationTask)
        .where(
            ScriptGenerationTask.project_id == project_id,
            ScriptGenerationTask.chapter_id == chapter_id,
        )
        .order_by(ScriptGenerationTask.created_at.desc())
        .limit(1)
    )


def submit(db, ctx, data, retry_task_id=None):
    service._get_chapter(db, ctx.project.id, data.chapter_id)
    # Serialize all submits for this chapter, including concurrent browser tabs.
    db.scalar(select(Chapter).where(Chapter.id == data.chapter_id).with_for_update())
    previous = latest(db, ctx.project.id, data.chapter_id)
    if previous and not (previous.status == "failed" and previous.id == retry_task_id):
        return previous
    task = ScriptGenerationTask(
        project_id=ctx.project.id,
        chapter_id=data.chapter_id,
        created_by=ctx.user.id,
        title=data.title,
        strategy=data.strategy,
    )
    db.add(task)
    db.flush()
    return task


def run_once():
    with SessionLocal() as db:
        task = db.scalar(
            select(ScriptGenerationTask)
            .where(
                (ScriptGenerationTask.status == "queued")
                | (
                    (ScriptGenerationTask.status == "running")
                    & (ScriptGenerationTask.updated_at < datetime.now(UTC) - timedelta(minutes=10))
                )
            )
            .order_by(ScriptGenerationTask.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if task is None:
            return False
        if task.status == "running":
            task.status = "failed"
            task.error = "生成任务被中断，未保存剧本。请检查后重试。"
            db.commit()
            return True
        task.status = "running"
        task_id = task.id
        db.commit()
        # Hold the row lock through execution: other workers cannot expire or run it.
        task = db.scalar(
            select(ScriptGenerationTask).where(ScriptGenerationTask.id == task_id).with_for_update()
        )
        try:
            user = db.get(User, task.created_by)
            project = db.get(Project, task.project_id)
            membership = db.scalar(
                select(Membership).where(
                    Membership.project_id == task.project_id, Membership.user_id == task.created_by
                )
            )
            if not user or not user.is_active or not membership or project.deleted_at:
                raise Forbidden("账号或项目权限已变更，请检查后重试")
            ctx = ProjectContext(project, membership, user)
            require(ctx.role, "narrative.edit")
            script = service.generate_script_from_chapter(
                db,
                ctx,
                schemas.GenerateScriptIn(
                    chapter_id=task.chapter_id, title=task.title, strategy=task.strategy
                ),
            )
            task.script_id, task.status = script.id, "succeeded"
            db.commit()  # Script and completion receipt are atomic.
        except Exception as exc:
            db.rollback()
            task = db.get(ScriptGenerationTask, task_id)
            task.status = "failed"
            task.error = exc.message if isinstance(exc, AppError) else "剧本生成失败，请稍后重试"
            db.commit()
            log.warning("Script generation failed: %s", type(exc).__name__)
    return True


def start():
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                from app.modules.narrative.tool_tasks import run_once as run_tool
                worked = run_once()
                worked = run_tool() or worked
            except Exception:
                log.exception("Script generation queue unavailable")
                worked = False
            stop.wait(0.25 if worked else 2)

    thread = threading.Thread(target=loop, name="script-generation", daemon=True)
    thread.start()

    def shutdown():
        stop.set()
        thread.join(timeout=5)

    shutdown.is_alive = thread.is_alive
    return shutdown
