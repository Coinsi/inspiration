"""FastAPI 应用入口。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.errors import AppError, app_error_handler
from app.modules.asset.router import router as asset_router
from app.modules.identity.router import router as identity_router
from app.modules.narrative.router import router as narrative_router
from app.modules.generation.router import router as generation_router
from app.modules.media.router import router as media_router
from app.modules.prompt.router import router as prompt_router
from app.modules.review.router import router as review_router
from app.modules.shot.router import router as shot_router
from app.modules.timeline.router import router as timeline_router
from app.modules.assist.router import router as assist_router
from app.modules.setting.router import router as setting_router

app = FastAPI(
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
app.include_router(asset_router, prefix="/api/v1")
app.include_router(narrative_router, prefix="/api/v1")
app.include_router(prompt_router, prefix="/api/v1")
app.include_router(shot_router, prefix="/api/v1")
app.include_router(generation_router, prefix="/api/v1")
app.include_router(media_router, prefix="/api/v1")
app.include_router(review_router, prefix="/api/v1")
app.include_router(timeline_router, prefix="/api/v1")
app.include_router(assist_router, prefix="/api/v1")
app.include_router(setting_router, prefix="/api/v1")
# V1 模块已全部挂载


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}
