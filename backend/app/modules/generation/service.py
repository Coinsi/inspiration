"""generation 业务:供应商/配额配置、提交生成、任务编排(溯源)、变体钦定。"""
import time
import urllib.request
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

import base64

import app.adapters.generation  # noqa: F401  触发供应商注册
from app.adapters.contracts import GenerationRequest, ReferenceImage, generation_registry
from app.core import audit
from app.core.crypto import decrypt, encrypt
from app.core.deps import ProjectContext
from app.core.errors import CapabilityUnsupported, NotFound, QuotaExceeded
from app.models.enums import JobStatus, QuotaScope, RequestType
from app.models.generation import Generation, GenerationJob, ProviderConfig, Quota
from app.models.identity import Project
from app.modules.generation import schemas
from app.modules.shot import service as shot_service
from app.storage import cas


# ── 供应商配置 ──
def configure_provider(db: Session, ctx: ProjectContext, data: schemas.ProviderConfigIn) -> ProviderConfig:
    pc = db.scalar(
        select(ProviderConfig).where(
            ProviderConfig.project_id == ctx.project.id,
            ProviderConfig.provider_name == data.provider_name,
        )
    )
    if pc is None:
        pc = ProviderConfig(project_id=ctx.project.id, provider_name=data.provider_name)
        db.add(pc)
    pc.kind = data.kind
    pc.enabled = data.enabled
    pc.endpoint = data.endpoint
    pc.config = data.config
    pc.capabilities = data.capabilities
    if data.token is not None:
        pc.credentials_encrypted = encrypt(data.token)
    db.flush()
    return pc


def list_providers(db: Session, project_id: uuid.UUID) -> list[ProviderConfig]:
    return list(db.scalars(select(ProviderConfig).where(ProviderConfig.project_id == project_id)))


# ── 配额 ──
def set_quota(db: Session, ctx: ProjectContext, data: schemas.QuotaIn) -> Quota:
    q = db.scalar(
        select(Quota).where(
            Quota.project_id == ctx.project.id,
            Quota.scope == data.scope,
            Quota.user_id == data.user_id,
        )
    )
    if q is None:
        q = Quota(project_id=ctx.project.id, scope=QuotaScope(data.scope), user_id=data.user_id)
        db.add(q)
    q.limit_cost = data.limit_cost
    q.period = data.period
    db.flush()
    return q


def test_provider(
    db: Session, project_id: uuid.UUID, name: str, kind: str,
    endpoint: str | None, token: str | None, config: dict | None,
) -> tuple[bool, str]:
    """测试供应商连接:LLM 调 /models 探活鉴权;即梦探端点可达;mock 直接通过。"""
    import urllib.error
    import urllib.request

    from app.core.config import settings as _s

    pc = db.scalar(
        select(ProviderConfig).where(
            ProviderConfig.project_id == project_id, ProviderConfig.provider_name == name
        )
    )
    ep = endpoint or (pc.endpoint if pc else None)
    key = token or (decrypt(pc.credentials_encrypted) if pc and pc.credentials_encrypted else None)
    model = (config or {}).get("model") or (pc.config.get("model") if pc and pc.config else None)

    if name == "mock":
        return True, "Mock 供应商可用(离线占位)"

    if kind == "llm" or name == "cloud_llm":
        base = (ep or _s.llm_base_url).rstrip("/")
        key = key or _s.llm_api_key
        if not key:
            return False, "未配置 API Key"
        try:
            req = urllib.request.Request(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
                if 200 <= r.status < 300:
                    return True, f"连接成功 · 鉴权通过(模型 {model or _s.llm_model})"
            return True, "连接成功"
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return False, f"鉴权失败({e.code}):API Key 无效或无权限"
            if e.code == 404:
                return False, "端点 404:Base URL 可能不对(应为 OpenAI 兼容 /v1)"
            return False, f"HTTP {e.code}:{e.reason}"
        except Exception as exc:  # noqa: BLE001
            return False, f"无法连接:{exc}"

    if name == "gpt_image":
        base = (ep or "https://api.openai.com/v1").rstrip("/")
        if not key:
            return False, "未配置 API Key"
        try:
            req = urllib.request.Request(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
                if 200 <= r.status < 300:
                    return True, f"连接成功 · 鉴权通过(模型 {model or 'gpt-image-1'})"
            return True, "连接成功"
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return False, f"鉴权失败({e.code}):API Key 无效或无权限"
            if e.code == 404:
                return False, "端点 404:Base URL 可能不对(应为 OpenAI 兼容 /v1)"
            return False, f"HTTP {e.code}:{e.reason}"
        except Exception as exc:  # noqa: BLE001
            return False, f"无法连接:{exc}"

    if name == "jimeng":
        if not ep or not key:
            return False, "未配置端点或密钥"
        try:
            urllib.request.urlopen(urllib.request.Request(ep), timeout=10)  # noqa: S310
            return True, "端点可达(即梦正式鉴权将随真实 API 对接进一步校验)"
        except urllib.error.HTTPError:
            return True, "端点可达(返回非 2xx 属正常,鉴权待真实 API 对接)"
        except Exception as exc:  # noqa: BLE001
            return False, f"无法连接端点:{exc}"

    return False, f"未知供应商:{name}"


def get_project_quota(db: Session, project_id: uuid.UUID) -> Quota | None:
    return db.scalar(
        select(Quota).where(
            Quota.project_id == project_id, Quota.scope == QuotaScope.project, Quota.user_id.is_(None)
        )
    )


# ── 供应商实例 ──
def _provider_instance(db: Session, project_id: uuid.UUID, name: str):
    pc = db.scalar(
        select(ProviderConfig).where(
            ProviderConfig.project_id == project_id, ProviderConfig.provider_name == name
        )
    )
    kwargs = {}
    if pc is not None:
        kwargs = {"endpoint": pc.endpoint, "token": decrypt(pc.credentials_encrypted), "config": pc.config}
    return generation_registry.create(name, **kwargs)


def _final_prompt(db: Session, project_id: uuid.UUID, target_type: str, target_id: uuid.UUID, override: str | None) -> str:
    if override:
        return override
    if target_type == "shot":
        return shot_service.compose_prompt(db, project_id, target_id).final_prompt
    if target_type == "asset":
        # 资产概念图:名字 + 视觉身份提示词片段
        from app.models.asset import Asset
        from app.models.consistency import VisualIdentity
        from app.models.prompt import PromptFragment

        asset = db.get(Asset, target_id)
        if asset is None:
            return ""
        parts = [asset.name, asset.summary or ""]
        vi = db.scalar(select(VisualIdentity).where(VisualIdentity.asset_id == target_id))
        if vi is not None and vi.prompt_fragment_id:
            frag = db.get(PromptFragment, vi.prompt_fragment_id)
            if frag:
                parts.append(frag.text)
        return ", ".join(p for p in parts if p and p.strip())
    return ""


def _collect_references(
    db: Session, project_id: uuid.UUID, target_type: str, target_id: uuid.UUID
) -> list[ReferenceImage]:
    """收集目标对象的参考图(目前:资产的 AssetReferenceImage,按 ordinal 排序)。

    只返回 blob_hash + role(轻量);提交前由 _materialize_references 物化为 base64。
    """
    if target_type != "asset":
        return []
    from app.models.asset import AssetReferenceImage

    rows = db.scalars(
        select(AssetReferenceImage)
        .where(AssetReferenceImage.asset_id == target_id)
        .order_by(AssetReferenceImage.ordinal)
    )
    return [ReferenceImage(blob_hash=r.blob_hash, role=r.role) for r in rows]


def _materialize_references(db: Session, refs: list[ReferenceImage]) -> list[ReferenceImage]:
    """把参考图物化为可直接发送的 base64 data URI(本地 fs / MinIO 通用)。

    收口在此:开发(fs)与外部云供应商都能用;未来如需对 MinIO 改走预签名 URL,只改这里。
    """
    from app.models.storage import Blob

    out: list[ReferenceImage] = []
    for r in refs:
        blob = db.get(Blob, r.blob_hash)
        if blob is None:
            continue
        b64 = base64.b64encode(cas.read_bytes(blob)).decode()
        out.append(ReferenceImage(
            blob_hash=r.blob_hash, role=r.role, data_url=f"data:{blob.mime};base64,{b64}"
        ))
    return out


def _build_request(
    prompt: str, gen_in: schemas.GenerateIn, references: list[ReferenceImage] | None = None
) -> GenerationRequest:
    return GenerationRequest(
        request_type=gen_in.request_type.value,
        prompt=prompt,
        params=gen_in.params,
        provider_params=gen_in.provider_params,
        count=gen_in.count,
        references=references or [],
    )


# ── 成本预估 ──
def estimate(db: Session, ctx: ProjectContext, target_type: str, target_id: uuid.UUID, gen_in: schemas.GenerateIn) -> schemas.EstimateOut:
    provider = _provider_instance(db, ctx.project.id, gen_in.provider)
    prompt = _final_prompt(db, ctx.project.id, target_type, target_id, gen_in.prompt_override)
    est = provider.estimate_cost(_build_request(prompt, gen_in))
    return schemas.EstimateOut(points=est.points, detail=est.detail)


# ── 提交生成 ──
def submit(db: Session, ctx: ProjectContext, target_type: str, target_id: uuid.UUID, gen_in: schemas.GenerateIn) -> GenerationJob:
    provider = _provider_instance(db, ctx.project.id, gen_in.provider)
    caps = provider.capabilities()
    if gen_in.request_type.value not in caps.modalities:
        raise CapabilityUnsupported(
            f"供应商 {gen_in.provider} 不支持 {gen_in.request_type.value}",
            {"modalities": list(caps.modalities)},
        )
    prompt = _final_prompt(db, ctx.project.id, target_type, target_id, gen_in.prompt_override)

    # 收集参考图(资产生图):按供应商能力上限截断
    refs: list[ReferenceImage] = []
    if gen_in.use_references and "reference" in caps.features:
        refs = _collect_references(db, ctx.project.id, target_type, target_id)
        if caps.max_reference_images:
            refs = refs[: caps.max_reference_images]
    req = _build_request(prompt, gen_in, refs)
    est = provider.estimate_cost(req)

    # 配额校验
    quota = get_project_quota(db, ctx.project.id)
    if quota is not None and quota.limit_cost and float(quota.used_cost) + est.points > float(quota.limit_cost):
        raise QuotaExceeded(
            "项目点数不足",
            {"limit": float(quota.limit_cost), "used": float(quota.used_cost), "need": est.points},
        )

    job = GenerationJob(
        project_id=ctx.project.id, target_type=target_type, target_id=target_id,
        provider=gen_in.provider, request_type=gen_in.request_type, status=JobStatus.pending,
        params=gen_in.params, estimated_cost=est.points, created_by=ctx.user.id,
        input_snapshot={
            "prompt": prompt, "request_type": gen_in.request_type.value,
            "params": gen_in.params, "provider_params": gen_in.provider_params, "count": gen_in.count,
            "references": [{"blob_hash": r.blob_hash, "role": r.role} for r in refs],
        },
    )
    db.add(job)
    db.flush()
    audit.record(db, action="generation.submit", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=target_type, target_id=target_id, detail={"job": str(job.id), "est": est.points})

    from app.core.config import settings
    if settings.celery_eager:
        run_job(db, job.id)  # 同步执行(同会话)
    else:
        db.commit()
        from app.tasks.generation_tasks import run_generation_job
        run_generation_job.delay(str(job.id))
    return job


# ── 任务执行(溯源核心) ──
def run_job(db: Session, job_id: uuid.UUID) -> None:
    job = db.get(GenerationJob, job_id)
    if job is None:
        return
    job.status = JobStatus.submitted
    db.flush()
    snap = job.input_snapshot
    # 从轻量快照重建参考图引用,并在提交前物化为 base64(避免把大图存进快照)
    snap_refs = [ReferenceImage(blob_hash=r["blob_hash"], role=r.get("role", "ref"))
                 for r in snap.get("references", [])]
    materialized = _materialize_references(db, snap_refs)
    req = GenerationRequest(
        request_type=snap["request_type"], prompt=snap["prompt"],
        params=snap.get("params", {}), provider_params=snap.get("provider_params", {}),
        count=snap.get("count", 1), references=materialized,
    )
    provider = _provider_instance(db, job.project_id, job.provider)
    try:
        handle = provider.submit(req)
        job.external_job_id = handle.external_job_id
        job.status = JobStatus.running
        db.flush()
        result = provider.poll(handle)
        for _ in range(30):  # 轮询直到完成(mock 立即完成)
            if result.status != "running":
                break
            time.sleep(1)
            result = provider.poll(handle)
    except Exception as exc:  # noqa: BLE001
        job.status = JobStatus.failed
        job.error = str(exc)
        db.flush()
        return

    if result.status != "succeeded":
        job.status = JobStatus.failed
        job.error = result.error or "生成失败"
        db.flush()
        return

    points = float(job.estimated_cost or 0)
    per = points / max(len(result.outputs), 1)
    for out in result.outputs:
        data = out.data
        if data is None and out.url:
            with urllib.request.urlopen(out.url, timeout=120) as r:  # noqa: S310
                data = r.read()
        if data is None:
            continue
        mime = "video/mp4" if out.type == "video" else "image/png"
        blob = cas.put_bytes(db, data, mime)
        db.add(Generation(
            project_id=job.project_id, job_id=job.id, target_type=job.target_type,
            target_id=job.target_id, provider=job.provider, output_type=RequestType(out.type),
            output_blob_hash=blob.hash, prompt_snapshot=snap["prompt"], params=snap.get("params", {}),
            input_refs={
                "provider_params": snap.get("provider_params", {}),
                "references": [
                    {"blob_hash": r.blob_hash, "role": r.role, "materialized": bool(r.data_url)}
                    for r in materialized
                ],
            },
            cost_points=per, cost_raw=result.cost_raw,
        ))
    job.status = JobStatus.succeeded
    job.actual_cost = points

    quota = get_project_quota(db, job.project_id)
    if quota is not None:
        quota.used_cost = float(quota.used_cost) + points
    db.flush()


# ── 变体 ──
def list_generations(db: Session, project_id: uuid.UUID, target_type: str, target_id: uuid.UUID) -> list[Generation]:
    return list(
        db.scalars(
            select(Generation).where(
                Generation.project_id == project_id,
                Generation.target_type == target_type,
                Generation.target_id == target_id,
            ).order_by(Generation.created_at.desc())
        )
    )


def get_job(db: Session, project_id: uuid.UUID, job_id: uuid.UUID) -> GenerationJob:
    j = db.get(GenerationJob, job_id)
    if j is None or j.project_id != project_id:
        raise NotFound("任务不存在")
    return j


def patch_generation(db: Session, project_id: uuid.UUID, gen_id: uuid.UUID, data: schemas.RatePatch) -> Generation:
    g = db.get(Generation, gen_id)
    if g is None or g.project_id != project_id:
        raise NotFound("生成记录不存在")
    if data.rating is not None:
        g.rating = data.rating
    if data.is_favorite is not None:
        g.is_favorite = data.is_favorite
    db.flush()
    return g


def select_variant(db: Session, ctx: ProjectContext, gen_id: uuid.UUID) -> Generation:
    g = db.get(Generation, gen_id)
    if g is None or g.project_id != ctx.project.id:
        raise NotFound("生成记录不存在")
    # 同 target 下取消其他钦定
    for other in list_generations(db, ctx.project.id, g.target_type, g.target_id):
        other.is_selected = other.id == g.id
    if g.target_type == "shot":
        from app.models.shot import Shot

        shot = db.get(Shot, g.target_id)
        if shot is not None:
            shot.selected_generation_id = g.id
    elif g.target_type == "asset":
        # 钦定的概念图作为资产代表图(头像)
        from app.models.asset import Asset

        asset = db.get(Asset, g.target_id)
        if asset is not None and g.output_blob_hash:
            asset.representative_blob_hash = g.output_blob_hash
    audit.record(db, action="generation.select", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=g.target_type, target_id=g.target_id, detail={"generation": str(g.id)})
    db.flush()
    return g
