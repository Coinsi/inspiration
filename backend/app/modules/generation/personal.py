"""Private model catalog and generation. Project members cannot spend another user's key."""

import copy
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.contracts import JobHandle, generation_registry
from app.core.crypto import decrypt, encrypt
from app.core.database import get_db
from app.core.deps import get_current_user, require_action
from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.generation import GenerationJob, ProviderConfig
from app.models.identity import User
from app.models.personal_model import PersonalModelService
from app.modules.generation import presets

PREFIX = "personal_"
router = APIRouter(tags=["personal-model-services"])
DB = Depends(get_db, scope="function")
USER = Depends(get_current_user)
MANAGE = Depends(require_action("provider.manage"))


def profile(spec):
    return {
        **spec,
        "display_name": spec["name"],
        "param_schema": {k: {"enum": v} for k, v in spec.get("parameters", {}).items()},
    }


def owned(db, user_id, ident, lock=False):
    query = select(PersonalModelService).where(
        PersonalModelService.id == ident, PersonalModelService.user_id == user_id
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise NotFound("个人模型服务不存在或不属于当前账号")
    return row


def public(row):
    return {
        "id": row.id,
        "name": row.name,
        "preset_id": row.preset_id,
        "endpoint": row.endpoint,
        "enabled": row.enabled,
        "revision": row.revision,
        "has_key": bool(row.credentials_encrypted),
        "models": [
            {
                "id": key,
                "name": spec["name"],
                "modality": spec["modality"],
                "verification": row.verification.get(key, {"status": "unverified"}),
            }
            for key, spec in row.models.items()
            if spec.get("enabled", True)
        ],
        "catalog": [
            {"id": key, "name": spec["name"], "modality": spec["modality"]}
            for key, spec in row.models.items()
        ],
    }


@router.get("/me/model-services/presets")
def preset_list(user=USER):
    return presets.catalog()


@router.get("/me/model-services")
def list_services(user=USER, db: Session = DB):
    return [
        public(r)
        for r in db.scalars(
            select(PersonalModelService)
            .where(PersonalModelService.user_id == user.id)
            .order_by(PersonalModelService.created_at)
        )
    ]


class Save(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset_id: str
    token: SecretStr | None = None
    models: list[str] = Field(min_length=1, max_length=30)
    revision: int | None = None


@router.put("/me/model-services/{preset_id}")
def save(preset_id: str, data: Save, user=USER, db: Session = DB):
    preset = presets.PRESETS.get(preset_id)
    if not preset and preset_id.startswith("custom-"):
        saved = owned(db, user.id, uuid.uuid5(user.id, "personal-model-service:" + preset_id))
        preset = {"name": saved.name, "endpoint": saved.endpoint, "models": saved.models}
    if (
        not preset
        or data.preset_id != preset_id
        or any(k not in preset["models"] for k in data.models)
    ):
        raise CapabilityUnsupported("请选择目录中支持的模型")
    ident = uuid.uuid5(user.id, "personal-model-service:" + preset_id)
    # Serialize first-time saves as well as updates.
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    row = db.get(PersonalModelService, ident)
    token = data.token.get_secret_value().strip() if data.token else ""
    if row and data.revision != row.revision:
        raise Conflict("服务已更新，请重新打开后保存")
    if not row and not token:
        raise CapabilityUnsupported("首次连接需要填写 API Key")
    if not row:
        row = PersonalModelService(
            id=ident,
            user_id=user.id,
            preset_id=preset_id,
            name=preset["name"],
            endpoint=preset["endpoint"],
            revision=0,
            credentials_encrypted=encrypt(token),
            models={},
            verification={},
        )
        db.add(row)
    if token:
        row.credentials_encrypted = encrypt(token)
        row.verification = {}
    updated_models = {k: copy.deepcopy(preset["models"][k]) for k in dict.fromkeys(data.models)}
    if preset_id.startswith("custom-"):
        # Keep the imported catalog so deselected models can be enabled again.
        updated_models = {
            k: {**copy.deepcopy(spec), "enabled": k in data.models}
            for k, spec in preset["models"].items()
        }
    if row.revision and not token and updated_models == row.models:
        return public(row)
    row.models = updated_models
    row.verification = {k: v for k, v in row.verification.items() if k in row.models}
    row.enabled = True
    row.revision += 1
    db.flush()
    return public(row)


@router.post("/projects/{project_id}/model-channels/{channel_id}/use-personally")
def adopt(channel_id: uuid.UUID, ctx=MANAGE, db: Session = DB):
    from app.modules.generation.channels import channel, public_model

    source = channel(db, ctx.project.id, channel_id)
    preset_id = next(
        (key for key, value in presets.PRESETS.items() if value["endpoint"] == source.endpoint),
        None,
    )
    if not source.credentials_encrypted:
        raise CapabilityUnsupported("请先为项目服务保存密钥")
    preset_id = preset_id or "custom-" + source.id.hex
    ident = uuid.uuid5(ctx.user.id, "personal-model-service:" + preset_id)
    db.scalar(select(User).where(User.id == ctx.user.id).with_for_update())
    existing = db.get(PersonalModelService, ident)
    if existing:
        return public(existing)  # Never overwrite an existing personal key with a project key.
    configured = list(
        db.scalars(select(ProviderConfig).where(ProviderConfig.project_id == ctx.project.id))
    )
    selected = {}
    for p in configured:
        if not p.enabled or p.config.get("channel_id") != str(source.id):
            continue
        spec = public_model(p)
        key = next(
            (
                k
                for k, s in presets.PRESETS.get(preset_id, {}).get("models", {}).items()
                if s["model"] == spec["model"]
            ),
            p.id.hex,
        )
        selected[key] = {
            k: v
            for k, v in spec.items()
            if k not in ("id", "provider_name", "revision", "is_default")
        }
    if not selected:
        raise CapabilityUnsupported("这个项目服务尚未启用目录中的模型")
    row = PersonalModelService(
        id=ident,
        user_id=ctx.user.id,
        name=presets.PRESETS.get(preset_id, {}).get("name", source.name),
        preset_id=preset_id,
        endpoint=source.endpoint,
        credentials_encrypted=source.credentials_encrypted,
        enabled=True,
        revision=1,
        models=selected,
        verification={},
    )
    db.add(row)
    db.flush()
    return public(row)


def providers(db, user_id):
    if not user_id:
        return []
    result = []
    for row in db.scalars(
        select(PersonalModelService).where(PersonalModelService.user_id == user_id)
    ):
        for key, spec in row.models.items():
            if not spec.get("enabled", True):
                continue
            result.append(
                {
                    "id": uuid.uuid5(row.id, key),
                    "provider_name": f"{PREFIX}{row.id.hex}_{key}",
                    "kind": "llm" if spec["modality"] == "text" else "generation",
                    "enabled": row.enabled,
                    "endpoint": None,
                    "display_name": spec["name"],
                    "channel_name": row.name + " · 个人服务",
                    "capabilities": {},
                    "config": {
                        **profile(spec),
                        "scope": "personal",
                        "verification": row.verification.get(key, {"status": "unverified"}),
                    },
                }
            )
    return result


def text_config(db, name, user_id):
    from types import SimpleNamespace

    row, key = resolve(db, name, user_id)
    spec = row.models[key]
    if spec["modality"] != "text":
        raise CapabilityUnsupported("请选择文字模型")
    return SimpleNamespace(
        endpoint=row.endpoint,
        credentials_encrypted=row.credentials_encrypted,
        config=profile(spec),
        enabled=row.enabled,
    )


def resolve(db, name, user_id, snapshot=None):
    try:
        ident, key = name.removeprefix(PREFIX).split("_", 1)
        row = owned(db, user_id, uuid.UUID(ident))
    except (ValueError, AttributeError):
        raise NotFound("个人模型不存在") from None
    if not row.enabled or key not in row.models or not row.models[key].get("enabled", True):
        raise CapabilityUnsupported("个人服务或模型已停用，请重新选择")
    frozen = (snapshot or {}).get("personal_service")
    if frozen and (frozen.get("id") != str(row.id) or frozen.get("revision") != row.revision):
        raise Conflict("个人模型配置已变更，请核对原任务后重新创作")
    return row, key


class PersonalProvider:
    def __init__(self, db, name, user_id, snapshot=None):
        self.db, self.name, self.user_id = db, name, user_id
        self.row, self.key = resolve(db, name, user_id, snapshot)
        self.revision = self.row.revision
        self.config = profile(self.row.models[self.key])
        self.adapter = generation_registry.create(
            self.config["protocol"],
            endpoint=self.row.endpoint,
            token=decrypt(self.row.credentials_encrypted),
            config=self.config,
        )
        self.job_id = None

    def snapshot(self):
        return {
            "model_profile": self.config,
            "personal_service": {
                "id": str(self.row.id),
                "revision": self.revision,
                "key": self.key,
            },
        }

    def capabilities(self):
        caps = self.adapter.capabilities()
        caps.strict_parameters = True
        caps.modalities &= {self.config["modality"]}
        caps.param_schema = self.config["param_schema"]
        caps.max_reference_images = min(
            caps.max_reference_images, self.config.get("max_reference_images", 0)
        )
        if not caps.max_reference_images:
            caps.features -= {"reference", "img2img", "inpaint", "first_frame", "last_frame"}
        return caps

    def validate_request(self, req):
        return self.adapter.validate_request(req)

    def estimate_cost(self, req):
        return self.adapter.estimate_cost(req)

    def bind_job(self, job_id):
        self.job_id = job_id

    def phase(self, value, external_job_id=None):
        if self.job_id:
            job = self.db.get(GenerationJob, self.job_id)
            job.routing_state = {
                **(job.routing_state or {}),
                "phase": value,
                "provider_name": self.name,
                **({"external_job_id": external_job_id} if external_job_id else {}),
            }
            if external_job_id:
                job.external_job_id = external_job_id
            self.db.commit()

    def submit(self, req):
        from app.modules.generation.model_routing import SubmissionRejected

        if self.job_id:
            job = self.db.get(GenerationJob, self.job_id)
            resume = (job.routing_state or {}).get("resume")
            if resume:
                self.phase("accepted", resume["external_job_id"])
                return JobHandle(external_job_id=resume["external_job_id"])
        self.phase("dispatching")
        try:
            handle = self.adapter.submit(req)
        except SubmissionRejected:
            self.phase("rejected")
            raise
        except Exception:
            self.phase("submission_unknown")
            raise
        self.phase("accepted", handle.external_job_id)
        return handle

    def poll(self, handle):
        return self.adapter.poll(handle)

    def cancel(self, handle):
        return self.adapter.cancel(handle)


def record_result(db, job):
    """Only a completed real job verifies generation; saving a key never does."""
    frozen = job.input_snapshot.get("personal_service")
    if not frozen:
        return
    row = db.scalar(
        select(PersonalModelService)
        .where(
            PersonalModelService.id == uuid.UUID(frozen["id"]),
            PersonalModelService.user_id == job.created_by,
        )
        .with_for_update()
    )
    if not row or row.revision != frozen["revision"]:
        return
    row.verification = {
        **row.verification,
        frozen["key"]: {
            "status": "verified" if job.status == "succeeded" else "failed",
            "message": "已完成真实生成并保存结果"
            if job.status == "succeeded"
            else (job.error or "生成未完成"),
            "checked_at": datetime.now(UTC).isoformat(),
        },
    }
