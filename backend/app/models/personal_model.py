"""Account-owned model connections; never exposed through project membership."""

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class PersonalModelService(Base, TimestampMixin):
    __tablename__ = "personal_model_service"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    preset_id: Mapped[str] = mapped_column(String(60))
    endpoint: Mapped[str] = mapped_column(String(512))
    credentials_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    models: Mapped[dict] = mapped_column(JSONB, default=dict)
    verification: Mapped[dict] = mapped_column(JSONB, default=dict)
