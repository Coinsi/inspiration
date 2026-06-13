"""版本内核:Live 行 + 不可变快照(对照 03 §2.1)。

通用于所有可版本化实体(asset/novel/scene/shot/prompt...)。
- commit:把内容快照成一条不可变 version,version_no 自增。
- diff:字段级比较两个版本内容。
- rollback:取目标版本内容(由调用方写回 Live 行后再 commit 新版本)。
- lock:锁定版本,不可再改(基线候选)。
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.models.enums import VersionStatus
from app.models.versioning import Version


class VersioningService:
    def __init__(self, db: Session):
        self.db = db

    def _next_no(self, entity_type: str, entity_id: uuid.UUID) -> int:
        current = self.db.scalar(
            select(func.max(Version.version_no)).where(
                Version.entity_type == entity_type, Version.entity_id == entity_id
            )
        )
        return (current or 0) + 1

    def commit(
        self,
        *,
        project_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        content: dict,
        created_by: uuid.UUID | None = None,
        label: str | None = None,
        status: VersionStatus = VersionStatus.draft,
    ) -> Version:
        version = Version(
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            version_no=self._next_no(entity_type, entity_id),
            label=label,
            status=status,
            content=content,
            created_by=created_by,
        )
        self.db.add(version)
        self.db.flush()
        return version

    def list_versions(self, entity_type: str, entity_id: uuid.UUID) -> list[Version]:
        return list(
            self.db.scalars(
                select(Version)
                .where(Version.entity_type == entity_type, Version.entity_id == entity_id)
                .order_by(Version.version_no.desc())
            )
        )

    def get(self, version_id: uuid.UUID) -> Version:
        v = self.db.get(Version, version_id)
        if v is None:
            raise NotFound("版本不存在")
        return v

    def diff(self, from_id: uuid.UUID, to_id: uuid.UUID) -> dict:
        a, b = self.get(from_id), self.get(to_id)
        ac, bc = a.content or {}, b.content or {}
        keys = set(ac) | set(bc)
        changed, added, removed = {}, {}, {}
        for k in keys:
            if k not in ac:
                added[k] = bc[k]
            elif k not in bc:
                removed[k] = ac[k]
            elif ac[k] != bc[k]:
                changed[k] = {"from": ac[k], "to": bc[k]}
        return {
            "from": {"id": str(a.id), "version_no": a.version_no},
            "to": {"id": str(b.id), "version_no": b.version_no},
            "changed": changed,
            "added": added,
            "removed": removed,
        }

    def lock(self, version_id: uuid.UUID) -> Version:
        v = self.get(version_id)
        v.is_locked = True
        v.status = VersionStatus.locked
        self.db.flush()
        return v

    def content_of(self, version_id: uuid.UUID) -> dict:
        """取某版本内容(用于回滚:调用方写回 Live 行)。"""
        v = self.get(version_id)
        if v.entity_id is None:
            raise Conflict("版本数据异常")
        return v.content or {}
