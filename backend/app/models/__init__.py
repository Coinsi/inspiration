"""聚合导入所有模型,确保 Alembic 元数据完整(M0 一次性建全表)。"""
from app.models.base import Base  # noqa: F401
from app.models import (  # noqa: F401
    identity,
    engineering,
    harness,
    website,
    auth,
    versioning,
    storage,
    narrative,
    asset,
    consistency,
    prompt,
    shot,
    generation,
    creation,
    personal_model,
    timeline,
    review,
    assist,
    setting,
    library,
    media_index,
    media_processing,
    canvas,
    agent,
    conversation,
    skill,
    director,
    evidence,
    transcription,
)

from app.models.material import GeneratedMaterialState  # noqa: F401
from app.models.work import CreativeWork  # noqa: F401
