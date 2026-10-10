"""Durable route attempts. Ambiguous submission failures never trigger failover."""

import copy
import hashlib
from datetime import UTC, datetime

from sqlalchemy import select

from app.adapters.contracts import GenerationProvider, JobHandle, generation_registry
from app.core.crypto import decrypt
from app.core.errors import CapabilityUnsupported, Conflict, ProviderError
from app.models.generation import GenerationJob
from app.modules.generation import model_catalog as catalog


RESUMABLE_PROTOCOLS = {"openai_video", "ark_video", "minimax_video"}


class SubmissionRejected(ProviderError):
    """Only raised by adapters for an explicit rejection before any upstream job exists."""


def retry_allowed(job):
    state = job.routing_state or {}
    if state.get("phase") in ("dispatching", "submission_unknown"):
        return False
    if state.get("phase") in ("accepted", "generated"):
        if job.provider.startswith("personal_"):
            return bool(job.input_snapshot.get("model_profile", {}).get("protocol") in RESUMABLE_PROTOCOLS
                        and state.get("external_job_id"))
        route = next(
            (
                r
                for r in job.input_snapshot.get("routing", {}).get("candidates", [])
                if r["provider_name"] == state.get("provider_name")
            ),
            None,
        )
        return bool(
            route
            and route["model_profile"].get("protocol") in RESUMABLE_PROTOCOLS
            and state.get("external_job_id")
        )
    return True


class RoutedProvider(GenerationProvider):
    def __init__(self, db, project_id, name, snapshot=None):
        self.db, self.project_id, self.name = db, project_id, name
        self.row = catalog.get_model(db, project_id, name)
        self.plan = copy.deepcopy((snapshot or {}).get("routing"))
        self.job_id = None
        self.active = None
        self.actual_route = None

    def capabilities(self):
        if not self.row.enabled and not self.plan:
            raise CapabilityUnsupported("创作模型已停用")
        return catalog.aggregate(self.db, self.row)

    def estimate_cost(self, req):
        self.plan = catalog.plan(self.db, self.row, req)
        return catalog.estimate_plan(self.plan)

    def snapshot(self):
        return {"routing": copy.deepcopy(self.plan)}

    def bind_job(self, job_id):
        self.job_id = job_id

    def _record(self, phase, route, message="", external_job_id=None):
        if not self.job_id:
            raise Conflict("生成线路必须绑定持久化任务")
        job = self.db.scalar(
            select(GenerationJob)
            .where(GenerationJob.id == self.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        state = copy.deepcopy(job.routing_state or {})
        # Recording a returned handle must still work after a concurrent local cancel.
        if phase == "dispatching" and job.status == "canceled":
            from app.modules.generation.media_engine import Canceled

            self.db.rollback()
            raise Canceled()
        state.update(
            phase=phase,
            provider_name=route["provider_name"],
            channel_name=route["channel_name"],
            model_name=route["model_name"],
            cost_points=route.get("cost_points"),
        )
        if external_job_id:
            state["external_job_id"] = external_job_id
            job.external_job_id = external_job_id
        state.setdefault("attempts", []).append(
            {
                "phase": phase,
                "provider_name": route["provider_name"],
                "channel_name": route["channel_name"],
                "message": message,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        job.routing_state = state
        self.db.commit()

    def _instance(self, route, resume=False):
        p, ch, reason = catalog.route_profile(self.db, self.project_id, route)
        if not p or not ch:
            raise CapabilityUnsupported(reason)
        if reason and not resume:
            raise CapabilityUnsupported(reason)
        if (
            str(ch.id) != route["channel_id"]
            or hashlib.sha256(ch.endpoint.encode()).hexdigest() != route["endpoint_hash"]
        ):
            raise CapabilityUnsupported("渠道地址已改变，请先核对原任务；不会向新地址重发旧任务")
        config = route["model_profile"]
        if not resume and (p.config.get("revision", 1) != config.get("revision", 1)):
            raise CapabilityUnsupported("线路模型配置已修改，请重新提交创作")
        return generation_registry.create(
            config["protocol"],
            endpoint=ch.endpoint,
            token=decrypt(ch.credentials_encrypted),
            config=config,
        )

    def submit(self, req):
        if not self.plan or not self.job_id:
            raise Conflict("缺少已冻结的模型路由")
        job = self.db.get(GenerationJob, self.job_id)
        resume = (job.routing_state or {}).get("resume")
        self.db.commit()
        if resume:
            route = next(
                (
                    c
                    for c in self.plan["candidates"]
                    if c["provider_name"] == resume["provider_name"]
                ),
                None,
            )
            if not route or route["model_profile"]["protocol"] not in RESUMABLE_PROTOCOLS:
                raise Conflict("此协议暂不支持恢复查询，请检查上游记录")
            self.active, self.actual_route = self._instance(route, True), route
            self._record(
                "accepted", route, "继续查询原视频任务，不重新生成", resume["external_job_id"]
            )
            return JobHandle(external_job_id=resume["external_job_id"])
        if not self.row.enabled:
            raise CapabilityUnsupported("创作模型已停用，请重新选择模型")
        last_error = "没有可继续提交的线路，请检查渠道状态或参数"
        for route in self.plan["candidates"]:
            try:
                self.active = self._instance(route)
            except CapabilityUnsupported as exc:
                self._record("skipped", route, exc.message)
                continue
            self.actual_route = route
            self._record("dispatching", route, "正在提交")
            try:
                handle = self.active.submit(req)
                if not handle.external_job_id:
                    raise ProviderError("上游未返回任务编号，请先核对上游记录")
            except SubmissionRejected as exc:
                self._record("rejected", route, exc.message)
                last_error = exc.message
                continue
            except Exception:
                self._record(
                    "submission_unknown", route, "无法确认上游是否创建任务，已停止自动切换"
                )
                raise ProviderError(
                    "无法确认上游是否已创建任务；已停止重发，请检查上游记录后再决定是否重新创作"
                ) from None
            self._record("accepted", route, "上游已接受，后续只查询此任务", handle.external_job_id)
            return handle
        raise ProviderError(last_error)

    def poll(self, handle):
        result = self.active.poll(handle)
        if result.status == "failed":
            self._record("upstream_failed", self.actual_route, "上游任务失败；不会自动换渠道重做")
        elif result.status == "succeeded":
            self._record("generated", self.actual_route, "上游已完成，正在保存结果")
        return result

    def cancel(self, handle):
        if self.active:
            self.active.cancel(handle)

    def actual_cost(self):
        return (self.actual_route or {}).get("cost_points") or 0
