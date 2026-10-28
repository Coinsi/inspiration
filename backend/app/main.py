"""FastAPI 应用入口。"""

from asyncio import to_thread
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.errors import AppError, app_error_handler
from app.modules.website.router import router as website_router
from app.modules.website.blog import router as blog_router
from app.modules.skill.router import router as skill_router
from app.modules.director.router import router as director_router
from app.modules.evidence.router import router as evidence_router
from app.modules.transcription.router import router as transcription_router
from app.modules.agent.router import router as agent_router
from app.modules.harness.router import router as production_router
from app.modules.harness.media_tools import register_tools as register_production_tools
from app.modules.harness.editing_tools import register_tools as register_editing_tools
from app.modules.harness.search_tools import register_tools as register_search_tools
from app.modules.harness.work_tools import register_tools as register_work_tools
from app.modules.harness.feedback_tools import register_tools as register_feedback_tools
from app.modules.harness.quality_tools import register_tools as register_quality_tools
from app.modules.harness.process_tools import register_tools as register_process_tools
from app.modules.harness.transcription_tools import register_tools as register_transcription_tools
from app.modules.harness.recovery_tools import register_tools as register_recovery_tools
from app.modules.harness.model_tools import register_tools as register_model_tools
from app.modules.conversation.router import router as conversation_router
from app.modules.attachments.router import router as attachments_router
from app.modules.canvas.router import router as canvas_router
from app.modules.asset.router import router as asset_router
from app.modules.assist.router import router as assist_router
from app.modules.generation.router import router as generation_router
from app.modules.generation.workbench import router as workbench_router
from app.modules.identity.router import router as identity_router
from app.modules.library.router import router as library_router
from app.modules.media.router import router as media_router
from app.modules.media.generation_reference import router as generation_reference_router
from app.modules.media.generation_reference import ReferenceOriginMiddleware
from app.modules.narrative.router import router as narrative_router
from app.modules.prompt.router import router as prompt_router
from app.modules.review.router import router as review_router
from app.modules.setting.router import router as setting_router
from app.modules.shot.router import router as shot_router
from app.modules.generation.channel_router import router as channel_router
from app.modules.generation.model_router import router as creative_model_router
from app.modules.generation.platform_router import router as platform_model_router
from app.modules.timeline.router import router as timeline_router

register_production_tools()
register_editing_tools()
register_search_tools()
register_work_tools()
register_feedback_tools()
register_quality_tools()
register_process_tools()
register_transcription_tools()
register_recovery_tools()
register_model_tools()


@asynccontextmanager
async def lifespan(app):
    from app.core.health import background_workers

    background_workers.clear()
    attachment_stop = None
    if settings.attachment_cleanup_enabled and not settings.celery_eager:
        from app.modules.attachments.cleanup import start as start_attachment_cleanup

        attachment_stop = start_attachment_cleanup()
        background_workers["attachment_cleanup"] = attachment_stop.is_alive
    processing_stop = None
    if settings.library_processing_enabled:
        from app.modules.library.processing_worker import start as start_processing

        processing_stop = start_processing()
        background_workers["media_processing"] = processing_stop.is_alive
    library_stop = None
    if settings.library_worker_enabled:
        from app.modules.library.worker import start

        library_stop = start()
        background_workers["library"] = library_stop.is_alive
    if settings.generation_executor == "local" and not settings.celery_eager:
        from app.modules.generation.jobs import recover_local

        recover_local()
    delivery_stop = None
    archive_stop = None
    if not settings.celery_eager:
        from app.modules.engineering.delivery import start as start_delivery

        delivery_stop = start_delivery()
        background_workers["engineering_delivery"] = delivery_stop.is_alive
        from app.modules.engineering.archive_jobs import start as start_archives

        archive_stop = start_archives()
        background_workers["engineering_archives"] = archive_stop.is_alive
    agent_stop = None
    production_stop = None
    transcription_stop = None
    if settings.transcription_worker_enabled:
        from app.modules.transcription.worker import start as start_transcription
        transcription_stop = start_transcription()
        background_workers["transcription"] = transcription_stop.is_alive
    if settings.agent_worker_enabled:
        from app.modules.agent.worker import start as start_agent
        agent_stop = start_agent()
        background_workers["agent"] = agent_stop.is_alive
        from app.modules.harness.runtime import start as start_production
        production_stop = start_production()
        background_workers["production"] = production_stop.is_alive
    canvas_stop = None
    if settings.canvas_worker_enabled:
        from app.modules.canvas.worker import start as start_canvas
        canvas_stop = start_canvas()
        background_workers["canvas"] = canvas_stop.is_alive
    try:
        yield
    finally:
        if attachment_stop:
            await to_thread(attachment_stop)
        if archive_stop:
            await to_thread(archive_stop)
        if delivery_stop:
            await to_thread(delivery_stop)
        if processing_stop:
            await to_thread(processing_stop)
        if transcription_stop:
            await to_thread(transcription_stop)
        if agent_stop:
            await to_thread(agent_stop)
        if production_stop:
            await to_thread(production_stop)
        if canvas_stop:
            await to_thread(canvas_stop)
        if library_stop:
            await to_thread(library_stop)
        background_workers.clear()


app = FastAPI(
    lifespan=lifespan,
    title=f"{settings.app_name} API",
    version="0.1.0",
    description="影视/动漫 AIGC 资产管理与生产平台 —— 后端 API",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# CORS(前端联调)
app.add_middleware(ReferenceOriginMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_dev else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 统一错误处理
app.add_exception_handler(AppError, app_error_handler)

# 路由(API v1)
app.include_router(identity_router, prefix="/api/v1")
app.include_router(website_router, prefix="/api/v1")
app.include_router(blog_router, prefix="/api/v1")
app.include_router(skill_router, prefix="/api/v1")
app.include_router(director_router, prefix="/api/v1")
app.include_router(evidence_router, prefix="/api/v1")
app.include_router(transcription_router, prefix="/api/v1")
app.include_router(agent_router, prefix="/api/v1")
app.include_router(production_router, prefix="/api/v1")
app.include_router(conversation_router, prefix="/api/v1")
app.include_router(attachments_router, prefix="/api/v1")
from app.modules.conversation.works import router as works_router
app.include_router(works_router, prefix="/api/v1")
from app.modules.engineering.router import router as engineering_router
app.include_router(engineering_router, prefix="/api/v1")
from app.modules.activity.router import router as activity_router
app.include_router(activity_router, prefix="/api/v1")
app.include_router(canvas_router, prefix="/api/v1")
app.include_router(asset_router, prefix="/api/v1")
app.include_router(narrative_router, prefix="/api/v1")
app.include_router(prompt_router, prefix="/api/v1")
app.include_router(shot_router, prefix="/api/v1")
app.include_router(generation_router, prefix="/api/v1")
app.include_router(workbench_router, prefix="/api/v1")
app.include_router(channel_router, prefix="/api/v1")
app.include_router(creative_model_router, prefix="/api/v1")
app.include_router(media_router, prefix="/api/v1")
app.include_router(generation_reference_router, prefix="/api/v1")
app.include_router(library_router, prefix="/api/v1")
app.include_router(review_router, prefix="/api/v1")
app.include_router(timeline_router, prefix="/api/v1")
app.include_router(assist_router, prefix="/api/v1")
app.include_router(setting_router, prefix="/api/v1")
# V1 模块已全部挂载


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


@app.get("/health/ready", tags=["system"])
def ready():
    from app.core.health import readiness

    result = readiness()
    return JSONResponse(result, status_code=200 if result["status"] == "ok" else 503)

from app.modules.generation.personal import router as personal_model_router
app.include_router(personal_model_router, prefix="/api/v1")
app.include_router(platform_model_router, prefix="/api/v1")
