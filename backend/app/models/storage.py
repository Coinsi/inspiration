"""storage:内容寻址存储(CAS)。二进制按 hash 去重一份。"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class Blob(Base, TimestampMixin):
    __tablename__ = "blob"

    hash: Mapped[str] = mapped_column(String(128), primary_key=True)  # 内容哈希(sha256)
    storage_uri: Mapped[str] = mapped_column(String(512), nullable=False)  # MinIO 对象路径
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class BlobDeletion(Base, TimestampMixin):
    """Durable operation captures an immutable physical identity, never just a hash."""

    __tablename__ = "blob_deletion"
    id: Mapped[uuid.UUID] = uuid_pk()
    blob_hash: Mapped[str] = mapped_column(String(128), index=True)
    storage_uri: Mapped[str] = mapped_column(String(512), index=True)
    status: Mapped[str] = mapped_column(String(16), default="marked", index=True)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(100))
