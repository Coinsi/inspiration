"""Derive engineering prerequisites from actual reads, never a model assertion."""

from app.core.errors import Conflict


def track(previous, arguments, result):
    records = list(previous)
    # Catalogue/overview reads locate objects. A concrete document read pins the
    # semantic dependency, allowing unrelated documents to be edited in parallel.
    if arguments.get("view") == "document":
        item = {"kind": "document", "id": result["id"], "revision": result["revision"]}
        if item not in records:
            records.append(item)
    if arguments.get("view") == "time" and arguments.get("time_query"):
        query = arguments["time_query"]
        if query.get("document_id") and query.get("revision"):
            item = {"kind": "document", "id": query["document_id"], "revision": query["revision"]}
            if item not in records:
                records.append(item)
    if len(records) > 64:
        raise Conflict("当前步骤读取的工程依赖过多，请拆分任务再继续")
    return records


def merge(context, declared=()):
    records = list(declared)
    for item in (context.input_context or {}).get("read_preconditions", []):
        if item not in records:
            records.append(item)
    return records
