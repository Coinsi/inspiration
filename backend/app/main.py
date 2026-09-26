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
from app.modules.canvas.router import router as canvas_router
from app.modules.asset.router import router as asset_router
from app.modules.assist.router import router as assist_router
from app.modules.generation.router import router as generation_router
from app.modules.identity.router import router as identity_router
from app.modules.library.router import router as library_router
from app.modules.media.router import router as media_router
from app.modules.narrative.router import router as narrative_router
from app.modules.prompt.router import router as prompt_router
from app.modules.review.router import router as review_router
from app.modules.setting.router import router as setting_router
from app.modules.shot.router import router as shot_router
from app.modules.generation.channel_router import router as channel_router
from app.modules.timeline.router import router as timeline_router


@asynccontextmanager
async def lifespan(app):
    library_stop = None
    if settings.library_worker_enabled:
        from app.modules.library.worker import start

        library_stop = start()
    if settings.generation_executor == "local" and not settings.celery_eager:
        from app.modules.generation.jobs import recover_local

        recover_local()
    agent_stop = None
    transcription_stop = None
    if settings.transcription_worker_enabled:
        from app.modules.transcription.worker import start as start_transcription
        transcription_stop = start_transcription()
    if settings.agent_worker_enabled:
        from app.modules.agent.worker import start as start_agent
        agent_stop = start_agent()
    canvas_stop = None
    if settings.canvas_worker_enabled:
        from app.modules.canvas.worker import start as start_canvas
        canvas_stop = start_canvas()
    try:
        yield
    finally:
        if transcription_stop:
            await to_thread(transcription_stop)
        if agent_stop:
            await to_thread(agent_stop)
        if canvas_stop:
            await to_thread(canvas_stop)
        if library_stop:
            await to_thread(library_stop)


app = FastAPI(
    lifespan=lifespan,
    title=f"{settings.app_name} API",
    version="0.1.0",
    description="影视/动漫 AIGC 资产管理与生产平台 —— 后端 API",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# CORS(前端联调)
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
app.include_router(canvas_router, prefix="/api/v1")
app.include_router(asset_router, prefix="/api/v1")
app.include_router(narrative_router, prefix="/api/v1")
app.include_router(prompt_router, prefix="/api/v1")
app.include_router(shot_router, prefix="/api/v1")
app.include_router(generation_router, prefix="/api/v1")
app.include_router(channel_router, prefix="/api/v1")
app.include_router(media_router, prefix="/api/v1")
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
