"""storage:内容寻址存储(CAS)。二进制按 hash 去重一份。"""
from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Blob(Base, TimestampMixin):
    __tablename__ = "blob"

    hash: Mapped[str] = mapped_column(String(128), primary_key=True)  # 内容哈希(sha256)
    storage_uri: Mapped[str] = mapped_column(String(512), nullable=False)  # MinIO 对象路径
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
