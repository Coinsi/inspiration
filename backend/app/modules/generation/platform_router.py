"""Independent platform administration, sharing model validation and encrypted storage."""

from fastapi import APIRouter

from app.modules.generation.channel_router import create_router as channel_router
from app.modules.generation.model_router import create_router as model_router
from app.modules.generation.model_scope import platform_context

router = APIRouter()
router.include_router(channel_router("/admin/models/model-channels", platform_context))
router.include_router(model_router("/admin/models/creative-models", platform_context))
