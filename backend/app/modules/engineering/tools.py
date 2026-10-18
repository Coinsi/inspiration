"""Small engineering tool surface. State and execution remain application-owned."""

import uuid
from typing import Literal

from pydantic import Field

from app.core.errors import Forbidden
from app.modules.engineering import schemas


class Read(schemas.Strict):
    work_id: uuid.UUID
    view: Literal["overview", "documents", "document", "change", "time"] = "overview"
    time_query: schemas.LocateTime | None = None
    document_id: uuid.UUID | None = None
    revision: int | None = Field(default=None, ge=1)
    change_id: uuid.UUID | None = None


class Propose(schemas.Propose):
    work_id: uuid.UUID


class Preview(schemas.Preview):
    work_id: uuid.UUID
    change_id: uuid.UUID


def scoped(context, work_id):
    selected = getattr(context, "work_id", None)
    if selected is None:
        selected = ((getattr(context, "input_context", None) or {}).get("work") or {}).get("id")
    if not selected or str(selected) != str(work_id):
        raise Forbidden("请先关联此作品，再通过工具提出修改或生成预览")


def read(db, ctx, context, args):
    from app.core.errors import CapabilityUnsupported
    from app.modules.engineering import documents, executors, impact, review, service

    work = service.work(db, ctx, args.work_id)
    if args.view == "time":
        from app.modules.engineering.locate import locate

        if args.time_query is None:
            raise CapabilityUnsupported("时间定位需要固定文档修订、实例和时间坐标")
        return locate(db, work.id, args.time_query)
    if args.view == "overview":
        return {
            **service.read(db, ctx, work.id),
            "preview_executors": executors.describe(),
            "adoption": "候选不会自动采用；用户在作品审阅页面确认",
        }
    if args.view == "documents":
        return {
            "documents": documents.index(db, work.id),
            "types": documents.document_types.contracts(),
        }
    if args.view == "document":
        if not args.document_id or not args.revision:
            raise CapabilityUnsupported("读取文档须提供固定文档 ID 和修订")
        return documents.sequence_view(db, work.id, args.document_id, args.revision)
    if not args.change_id:
        raise CapabilityUnsupported("读取候选须提供候选 ID")
    change = review.get_change(db, work.id, args.change_id)
    before = service.snapshot_at(db, work.id, change.base_revision)
    return {
        **review.details(db, ctx, change),
        "impact": impact.compare(
            db,
            work.id,
            before.get("manifest_ref"),
            change.snapshot.get("manifest_ref"),
            change.snapshot.get("derived_documents", []),
        ),
    }


def propose(db, ctx, context, args):
    from app.modules.engineering import service

    scoped(context, args.work_id)
    return service.propose(
        db, ctx, args.work_id, schemas.Propose.model_validate(args.model_dump(exclude={"work_id"}))
    )


def preview(db, ctx, context, args):
    from app.modules.engineering import review

    scoped(context, args.work_id)
    return review.preview(
        db,
        ctx,
        args.work_id,
        args.change_id,
        schemas.Preview.model_validate(args.model_dump(exclude={"work_id", "change_id"})),
    )


def register_tools(register, tool):
    register(
        tool(
            "engineering.read",
            "读取作品工程、文档固定修订或候选影响。先读取工程基线再修改。",
            Read,
            group="engineering",
        ),
        read,
    )
    register(
        tool(
            "engineering.propose",
            "为已关联作品提交原子修改候选，固定 base_revision 和 request_key；不自动采用。",
            Propose,
            group="engineering",
        ),
        propose,
    )
    register(
        tool(
            "engineering.preview",
            "预览已关联作品的固定候选；不支持的算子会拒绝执行。结果仍需用户采用。",
            Preview,
            group="engineering",
        ),
        preview,
    )
