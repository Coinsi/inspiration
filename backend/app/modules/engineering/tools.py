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
    from app.modules.harness.read_tracking import merge

    scoped(context, args.work_id)
    return service.propose(
        db,
        ctx,
        args.work_id,
        schemas.Propose.model_validate(
            {
                **args.model_dump(exclude={"work_id"}),
                "read_preconditions": merge(context, args.model_dump()["read_preconditions"]),
            }
        ),
    )


def preview(db, ctx, context, args):
    from app.modules.engineering import review

    scoped(context, args.work_id)
    execution = context.input_context.get("execution", {})
    execution_snapshot = None
    if execution.get("run_id"):
        from app.models.harness import ProductionRun

        run = db.get(ProductionRun, uuid.UUID(str(execution["run_id"])))
        key = "preview:" + str(execution.get("request_key"))
        entry = (run.context.get("resource_reservations", {}) if run else {}).get(key)
        if (
            not run
            or run.project_id != ctx.project.id
            or run.created_by != ctx.user.id
            or run.work_id != args.work_id
            or not entry
            or entry["reserved"].get("output_storage_bytes") != 512 * 1024 * 1024
        ):
            raise Forbidden("预览缺少有效的持久资源预留")
        execution_snapshot = {
            "harness_resource_limits": {
                "output_bytes": entry["reserved"]["output_storage_bytes"],
                "reservation_key": key,
            },
            "harness_origin": {
                "run_id": str(run.id),
                "task_id": execution.get("task_id"),
                "request_key": execution.get("request_key"),
                "tool": "engineering.preview",
            },
        }
    return review.preview(
        db,
        ctx,
        args.work_id,
        args.change_id,
        schemas.Preview.model_validate(args.model_dump(exclude={"work_id", "change_id"})),
        execution_snapshot=execution_snapshot,
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
