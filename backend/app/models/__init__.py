"""聚合导入所有模型,确保 Alembic 元数据完整(M0 一次性建全表)。"""
from app.models.base import Base  # noqa: F401
from app.models import (  # noqa: F401
    identity,
    versioning,
    storage,
    narrative,
    asset,
    consistency,
    prompt,
    shot,
    generation,
    timeline,
    review,
    assist,
    setting,
)
