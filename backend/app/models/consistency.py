"""consistency:角色/场景的视觉身份(供一致性策略消费)。"""
import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk
from app.models.enums import ConsistencyStrategyKind


class VisualIdentity(Base, TimestampMixin):
    __tablename__ = "visual_identity"

    id: Mapped[uuid.UUID] = uuid_pk()
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset.id", ondelete="CASCADE"), unique=True
    )
    strategy: Mapped[ConsistencyStrategyKind] = mapped_column(
        String(16), default=ConsistencyStrategyKind.prompt
    )
    prompt_fragment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompt_fragment.id")
    )
    lora_ref: Mapped[dict | None] = mapped_column(JSONB)
    reference_set: Mapped[dict | None] = mapped_column(JSONB)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
