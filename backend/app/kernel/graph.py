"""关系图内核:依赖边 + 反查 + 影响分析(对照 03 §2.2 / §四 4.4)。

- 通用 relation 边:承载溯源图(改编/组成/引用/复用/组装)。
- 资产引用热路径走强类型 shot_asset_ref(承载浮动/钉死)。
- usages:某资产被哪些镜头引用(反查)。
- impact:改某资产版本 → 下游受影响清单(floating 已波及 / pinned 可升级)。
"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import RefMode, RelationType
from app.models.shot import Shot, ShotAssetRef
from app.models.versioning import Relation


class GraphService:
    def __init__(self, db: Session):
        self.db = db

    # ── 通用边 ──
    def add_relation(
        self,
        *,
        project_id: uuid.UUID,
        src_type: str,
        src_id: uuid.UUID,
        dst_type: str,
        dst_id: uuid.UUID,
        rel_type: RelationType,
        ordinal: int | None = None,
    ) -> Relation:
        rel = Relation(
            project_id=project_id,
            src_type=src_type,
            src_id=src_id,
            dst_type=dst_type,
            dst_id=dst_id,
            rel_type=rel_type,
            ordinal=ordinal,
        )
        self.db.add(rel)
        self.db.flush()
        return rel

    def incoming(self, dst_type: str, dst_id: uuid.UUID) -> list[Relation]:
        return list(
            self.db.scalars(
                select(Relation).where(Relation.dst_type == dst_type, Relation.dst_id == dst_id)
            )
        )

    # ── 资产反查 ──
    def asset_usages(self, asset_id: uuid.UUID) -> list[dict]:
        """该资产被哪些镜头引用。"""
        rows = self.db.execute(
            select(ShotAssetRef, Shot)
            .join(Shot, Shot.id == ShotAssetRef.shot_id)
            .where(ShotAssetRef.asset_id == asset_id, Shot.deleted_at.is_(None))
        ).all()
        return [
            {
                "shot_id": str(shot.id),
                "shot_code": shot.code,
                "role": ref.role,
                "ref_mode": ref.ref_mode if isinstance(ref.ref_mode, str) else ref.ref_mode.value,
                "pinned_version_id": str(ref.pinned_version_id) if ref.pinned_version_id else None,
            }
            for ref, shot in rows
        ]

    # ── 影响分析 ──
    def asset_impact(self, asset_id: uuid.UUID) -> dict:
        """改该资产版本时:区分浮动引用(已自动波及)与钉死引用(可选择升级)。"""
        usages = self.asset_usages(asset_id)
        floating = [u for u in usages if u["ref_mode"] == RefMode.floating.value]
        pinned = [u for u in usages if u["ref_mode"] == RefMode.pinned.value]
        return {
            "asset_id": str(asset_id),
            "total": len(usages),
            "floating_affected": floating,  # 已自动波及,可一键重生成
            "pinned_upgradable": pinned,  # 需手动升级钉死版本
        }
