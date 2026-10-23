"""Shared application resource ceilings; reservations survive worker restarts."""

from sqlalchemy import func, select, text

from app.core.errors import QuotaExceeded
from app.models.harness import ProductionAttempt, ProductionRun, ProductionTask

LIMITS = {
    "model_tokens": ("max_model_tokens", 4000000),
    "output_storage_bytes": ("max_output_storage_bytes", 2147483648),
    "media_transfer_bytes": ("max_media_transfer_bytes", 4294967296),
}


def reserve_units(run, key, amounts, *, job_id=None):
    """Reserve before side effects. Unknown consumption never becomes zero."""
    if not amounts or any(
        unit not in LIMITS or not isinstance(amount, int) or isinstance(amount, bool) or amount < 0
        for unit, amount in amounts.items()
    ):
        raise QuotaExceeded("资源预留必须使用已知单位和非负整数")
    entries = dict(run.context.get("resource_reservations", {}))
    if key in entries:
        if entries[key]["reserved"] != amounts:
            raise QuotaExceeded("同一资源预留不能用于不同操作")
        if job_id and entries[key].get("job_id") not in {None, str(job_id)}:
            raise QuotaExceeded("同一资源预留不能用于另一个任务")
        if job_id and not entries[key].get("job_id"):
            entries[key] = {**entries[key], "job_id": str(job_id)}
            run.context = {**run.context, "resource_reservations": entries}
        return
    for unit, amount in amounts.items():
        field, default = LIMITS[unit]
        used = sum(
            e.get("actual", {}).get(unit, e["reserved"].get(unit, 0)) for e in entries.values()
        )
        if amount < 0 or used + amount > run.policy.get(field, default):
            raise QuotaExceeded(f"本次制作的资源预算不足（{unit}），已有成果已保留")
    entries[key] = {
        "reserved": dict(amounts),
        "actual": {},
        "status": "reserved",
        "job_id": str(job_id) if job_id else None,
    }
    run.context = {**run.context, "resource_reservations": entries}


def ensure_limits(run):
    for unit, state in summary(run).items():
        if state["measured"] + state["reserved_or_unknown"] > state["limit"]:
            raise QuotaExceeded(f"本次制作已超过资源限额（{unit}），请调整限额或停止制作")


def settle_units(run, key, actual):
    entries = dict(run.context.get("resource_reservations", {}))
    if key not in entries:
        return
    valid = {
        unit: amount
        for unit, amount in actual.items()
        if unit in entries[key]["reserved"]
        and isinstance(amount, int)
        and not isinstance(amount, bool)
        and amount >= 0
    }
    entries[key] = {
        **entries[key],
        "actual": {**entries[key].get("actual", {}), **valid},
        "status": "measured" if valid else "unknown",
    }
    run.context = {**run.context, "resource_reservations": entries}


def reserve_model(run, ticket, packet, media):
    import json

    from app.modules.harness.kernel import SYSTEM

    # Tokenization and media accounting differ by provider. This is an explicit
    # conservative reservation, not an invented billed-token measurement.
    text = len((SYSTEM + json.dumps(packet, ensure_ascii=False, default=str)).encode("utf-8"))
    media_allowance = 32768 * len(media)
    amounts = {
        "model_tokens": text + media_allowance + run.policy.get("max_model_output_tokens", 8192)
    }
    if media:
        # Count the application payload sent for this observation (including
        # base64 expansion), not a claim about provider/CDN billed wire traffic.
        amounts["media_transfer_bytes"] = len(json.dumps(media, default=str).encode("utf-8"))
    reserve_units(
        run,
        "model:" + ticket["attempt_id"],
        amounts,
    )


def settle_model(run, ticket, usage):
    value = (usage or {}).get("total_tokens") if isinstance(usage, dict) else None
    settle_units(
        run,
        "model:" + ticket["attempt_id"],
        {"model_tokens": value} if isinstance(value, int) else {},
    )


def settle_media(db, run, job_id):
    from app.models.generation import Generation
    from app.models.storage import Blob

    size = db.scalar(
        select(func.sum(Blob.size_bytes))
        .select_from(Generation)
        .join(Blob, Blob.hash == Generation.output_blob_hash)
        .where(Generation.job_id == job_id)
    )
    if size is None:
        return
    for key, entry in list(run.context.get("resource_reservations", {}).items()):
        if entry.get("job_id") == str(job_id):
            settle_units(
                run, key, {"output_storage_bytes": int(size), "media_transfer_bytes": int(size)}
            )


def summary(run):
    entries = run.context.get("resource_reservations", {})
    result = {}
    for unit, (field, default) in LIMITS.items():
        measured = sum(e.get("actual", {}).get(unit, 0) for e in entries.values())
        reserved = sum(
            e["reserved"].get(unit, 0) for e in entries.values() if unit not in e.get("actual", {})
        )
        limit = run.policy.get(field, default)
        result[unit] = {
            "measured": measured,
            "reserved_or_unknown": reserved,
            "limit": limit,
            "remaining": max(0, limit - measured - reserved),
        }
    return result


def active_seconds(db, run):
    from app.modules.harness.service import now

    attempts = db.scalars(
        select(ProductionAttempt).join(ProductionTask).where(ProductionTask.run_id == run.id)
    )
    total = 0.0
    for attempt in attempts:
        if "elapsed_ms" in attempt.operation:
            total += attempt.operation["elapsed_ms"] / 1000
        elif attempt.status == "started":
            total += max(0, (now() - attempt.created_at).total_seconds())
    return total


def capacity(db, run):
    # A short admission lock makes the cross-run count atomic between API workers.
    # Never hold it while a provider or renderer is running.
    db.execute(text("SELECT pg_advisory_xact_lock(91357, 12)"))
    if run.context.get("guidance_pending"):
        # One reserved read-only control lane per run. It cannot generate/edit.
        active_control = db.scalar(
            select(func.count())
            .select_from(ProductionTask)
            .where(
                ProductionTask.run_id == run.id,
                ProductionTask.role == "impact",
                ProductionTask.status == "running",
            )
        )
        return active_control == 0
    query = (
        select(func.count())
        .select_from(ProductionTask)
        .join(ProductionRun)
        .where(ProductionTask.status == "running")
    )
    if db.scalar(query.where(ProductionRun.created_by == run.created_by)) >= 3:
        return False
    if db.scalar(query.where(ProductionRun.project_id == run.project_id)) >= 4:
        return False
    if (
        run.model_provider
        and db.scalar(
            query.where(
                ProductionRun.project_id == run.project_id,
                ProductionRun.model_provider == run.model_provider,
            )
        )
        >= 2
    ):
        return False
    return True


def reserve_preview(db, run, ticket, arguments):
    # Conservatively retain failed/unknown reservations; a crash is not a refund.
    reservations = dict(run.context.get("preview_reservations", {}))
    key = ticket["attempt_id"]
    selected = {
        "change_id": arguments.get("change_id"),
        "output_id": arguments.get("output_id"),
    }
    if key in reservations and reservations[key] != selected:
        raise QuotaExceeded("同一预览预留不能用于不同候选或输出")
    if key not in reservations:
        if len(reservations) >= run.policy.get("max_preview_jobs", 12):
            raise QuotaExceeded("本次制作的预览次数已达到上限，已有预览和候选仍保留")
        reserve_units(
            run,
            "preview:" + key,
            {"output_storage_bytes": 512 * 1024 * 1024, "media_transfer_bytes": 512 * 1024 * 1024},
        )
        reservations[key] = selected
        run.context = {**run.context, "preview_reservations": reservations}


def record_elapsed(attempt):
    from app.modules.harness.service import now

    attempt.operation = {
        **attempt.operation,
        "elapsed_ms": max(0, round((now() - attempt.created_at).total_seconds() * 1000)),
    }
