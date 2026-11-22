"""Wallet reservation lifecycle for platform-funded image/video generation.

The durable dispatch marker is written before provider.submit. Once that marker
exists, absence of a receipt is not proof that the provider did not accept work.
Retries that only receive an existing upstream job share its original reservation.
"""

import uuid

from sqlalchemy import select

from app.adapters.contracts import GenerationRequest
from app.core.errors import CapabilityUnsupported, Conflict
from app.models.generation import GenerationJob, ProviderConfig
from app.modules.billing import wallet

KEY = "billing"


def _info(job):
    return (job.input_snapshot or {}).get(KEY, {})


def _root(job):
    return uuid.UUID(_info(job)["reservation_job_id"])


def _mark(job, phase, **detail):
    job.cost_raw = {
        **(job.cost_raw or {}),
        KEY: {**(job.cost_raw or {}).get(KEY, {}), "phase": phase, **detail},
    }


def platform_funded(db, job):
    if job.request_type not in ("image", "video"):
        return False
    if job.provider in ("local", "mock", "archive") or job.provider.startswith("personal_"):
        return False
    if job.provider.startswith("creative_"):
        from app.modules.generation.model_catalog import get_model

        return get_model(db, job.project_id, job.provider, allow_global=True).project_id is None
    scope = None if job.provider.startswith("platform_") else job.project_id
    row = db.scalar(
        select(ProviderConfig).where(
            ProviderConfig.provider_name == job.provider,
            ProviderConfig.project_id == scope,
        )
    )
    # A registered adapter with no project-owned configuration uses platform
    # defaults. It must not silently become a free path around priced models.
    return row is None or row.project_id is None


def quoted_units(job):
    snap = job.input_snapshot or {}
    routes = snap.get("routing", {}).get("candidates")
    if routes:
        amounts = [route.get("cost_points") for route in routes]
    else:
        from app.modules.generation.model_catalog import Price

        price = snap.get("model_profile", {}).get("price", {})
        if price.get("mode") not in ("per_request", "per_second"):
            raise CapabilityUnsupported("平台模型尚未配置有效积分价格，请联系管理员")
        try:
            amounts = [
                Price(**price).quote(
                    GenerationRequest(
                        request_type=job.request_type,
                        prompt=snap.get("prompt", ""),
                        params=snap.get("params", {}),
                        provider_params=snap.get("provider_params", {}),
                        count=snap.get("count", 1),
                    )
                )
            ]
        except (ValueError, TypeError) as exc:
            raise CapabilityUnsupported("平台模型积分价格无效") from exc
    if any(value is None or isinstance(value, bool) for value in amounts):
        raise CapabilityUnsupported("平台模型存在未配置积分价格的线路")
    units = [wallet.points_to_units(value) for value in amounts]
    if not units or min(units) <= 0:
        raise CapabilityUnsupported("平台模型积分价格必须大于零")
    return max(units)


def prepare(db, job):
    """Called after job flush and again before execution; never commits."""
    if _info(job):
        if wallet.generation_reservation_status(db, _root(job)) != "reserved":
            raise Conflict("生成积分预留记录缺失，请联系管理员")
        return
    if not wallet.payment_required(db) or not platform_funded(db, job):
        return
    if not job.created_by:
        raise CapabilityUnsupported("付费生成任务缺少付款用户")
    units = quoted_units(job)
    wallet.reserve_generation(db, job.created_by, job.id, units)
    job.input_snapshot = {
        **(job.input_snapshot or {}),
        KEY: {
            "reservation_job_id": str(job.id),
            "user_id": str(job.created_by),
            "reserved_units": units,
            "resume": False,
        },
    }
    _mark(job, "reserved", reservation_job_id=str(job.id), reserved_units=units)


def retry_snapshot(db, old, snapshot, *, resuming):
    info = _info(old)
    if not info:
        if wallet.payment_required(db) and old.external_job_id and not resuming:
            raise Conflict("上游任务可能已经计费，请先核对结果，不能直接重新生成")
        return
    root_id = _root(old)
    db.scalar(select(GenerationJob).where(GenerationJob.id == root_id).with_for_update())
    if resuming:
        if wallet.generation_reservation_status(db, root_id) != "reserved":
            raise Conflict("原生成积分预留记录缺失")
        active = db.scalar(
            select(GenerationJob.id).where(
                GenerationJob.project_id == old.project_id,
                GenerationJob.input_snapshot[KEY]["reservation_job_id"].astext == str(root_id),
                GenerationJob.status.in_(("pending", "submitted", "running", "succeeded")),
            )
        )
        if active:
            raise Conflict("原上游任务已有恢复任务或已完成，不会重复接收与计费")
        snapshot[KEY] = {**info, "resume": True}
    else:
        if (old.cost_raw or {}).get(KEY, {}).get("phase") != "released":
            raise Conflict("原任务积分仍在核对中，请先恢复原任务或联系管理员")
        snapshot.pop(KEY, None)


def dispatching(db, job):
    prepare(db, job)
    if _info(job):
        _mark(job, "dispatching", reservation_job_id=str(_root(job)))


def accepted(job):
    if _info(job):
        _mark(
            job,
            "held" if job.status == "canceled" else "accepted",
            external_job_id=job.external_job_id,
        )


def unsuccessful(db, job, *, explicitly_rejected=False):
    if not _info(job):
        return
    phase = (job.cost_raw or {}).get(KEY, {}).get("phase")
    routing = job.routing_state or {}
    attempts = routing.get("attempts", [])
    all_rejected = routing.get("phase") in ("skipped", "rejected") and all(
        attempt.get("phase") in ("dispatching", "rejected", "skipped") for attempt in attempts
    )
    safe = (
        not _info(job).get("resume")
        and not job.external_job_id
        and (phase in (None, "reserved", "released") or explicitly_rejected or all_rejected)
    )
    if safe:
        wallet.release_generation(db, _root(job))
        _mark(job, "released", reason="definitely_not_accepted")
    else:
        _mark(job, "held", reason="upstream_acceptance_or_charge_uncertain")


def settle(db, job, points):
    if not _info(job):
        return
    units = wallet.points_to_units(points)
    if not 0 < units <= _info(job)["reserved_units"]:
        raise Conflict("生成结算与预留报价不一致，积分保留等待核对")
    wallet.settle_generation(db, _root(job), units)
    _mark(job, "settled", settled_units=units, reservation_job_id=str(_root(job)))
