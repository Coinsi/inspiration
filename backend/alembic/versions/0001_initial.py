"""initial schema —— M0 一次性建全表(对照 03-详细设计-数据模型)

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-05
"""

import re
from collections.abc import Sequence
from pathlib import Path

from alembic import op
from app.core.config import settings

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')  # gen_random_uuid()
    if settings.enable_pgvector:  # EXT-01:相似检索预留,未启用则不依赖该扩展
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Frozen DDL prevents later model additions from being created twice on fresh installs.
    schema = Path(__file__).resolve().parents[1] / "initial_schema.sql"
    for statement in schema.read_text(encoding="utf-8").split("-- statement"):
        op.execute(statement.strip())
    if settings.enable_pgvector:
        op.execute("ALTER TABLE asset ADD COLUMN embedding vector(512)")
        op.execute("ALTER TABLE generation ADD COLUMN embedding vector(512)")


def downgrade() -> None:
    schema = Path(__file__).resolve().parents[1] / "initial_schema.sql"
    for name in reversed(re.findall(r"CREATE TABLE (\S+) \(", schema.read_text(encoding="utf-8"))):
        op.drop_table(name.strip('"'))
