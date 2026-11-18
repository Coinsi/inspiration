"""导入以触发生成供应商注册。"""
from app.adapters.generation import gpt_image, jimeng, mock, native_media, openai_video  # noqa: F401
from app.adapters.generation import video_v1  # noqa: F401

from app.adapters.generation import litellm_media  # noqa: F401

from app.adapters.contracts import generation_registry
from app.adapters.generation.provider_media import MEDIA_ADAPTERS

for adapter in MEDIA_ADAPTERS:
    generation_registry.register(adapter.name)(adapter)
