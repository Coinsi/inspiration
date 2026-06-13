"""initial schema —— M0 一次性建全表(对照 03-详细设计-数据模型)

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-05
"""
from collections.abc import Sequence

from alembic import op

from app.core.config import settings
from app.models import Base

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')  # gen_random_uuid()
    if settings.enable_pgvector:  # EXT-01:相似检索预留,未启用则不依赖该扩展
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # 由模型元数据一次性建全部表
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
