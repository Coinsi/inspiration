"""导入以触发生成供应商注册。"""
from app.adapters.contracts import generation_registry
from app.adapters.generation import (  # noqa: F401
    gpt_image,
    jimeng,
    litellm_media,
    minimax_h3,
    mock,
    native_media,
    openai_video,
    video_v1,
)
from app.adapters.generation.provider_media import MEDIA_ADAPTERS

for adapter in MEDIA_ADAPTERS:
    generation_registry.register(adapter.name)(adapter)
