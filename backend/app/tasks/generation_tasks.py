"""生成任务(异步)。eager 模式下由 service 直接同步调用 run_job;
真实部署经此 Celery 任务在 worker 中执行。"""
import uuid

from app.core.database import SessionLocal
from app.tasks.celery_app import celery_app


@celery_app.task(name="generation.run_job")
def run_generation_job(job_id: str) -> str:
    from app.modules.generation import service

    db = SessionLocal()
    try:
        service.run_job(db, uuid.UUID(job_id))
        db.commit()
        return "ok"
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
