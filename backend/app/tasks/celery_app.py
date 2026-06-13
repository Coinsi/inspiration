"""Celery 应用:异步生成 / 解析 / 拆解任务编排(M4 起填充任务)。"""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "inspiration",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    timezone="UTC",
    task_always_eager=settings.celery_eager,
    task_eager_propagates=settings.celery_eager,
)


@celery_app.task(name="health.ping")
def ping() -> str:
    return "pong"
