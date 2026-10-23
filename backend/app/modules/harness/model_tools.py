"""Read-only model discovery using the same catalog and adapters as the composer.

Only public capability fields cross the tool boundary. This does not probe a
provider, submit media, or grant permission to spend its credentials.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import AppError
from app.core.permissions import can
from app.modules.generation import schemas, service
from app.modules.harness.context import revalidate_context
from app.modules.harness.kernel_modalities import resolve_cloud_input
from app.modules.tools import registry
from app.modules.tools.capabilities import ToolPolicy, register_contract


class Models(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modality: Literal["text", "image", "video"] | None = None
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=30, ge=1, le=100)


def list_models(db, ctx, context, args):
    ctx = revalidate_context(db, ctx)
    models, unavailable = [], []
    # Keep the composer's order and hidden/disabled filtering. A routed model
    # replaces its hidden backing profiles, and personal services are user-owned.
    for raw in service.list_providers(db, ctx.project.id, user_id=ctx.user.id):
        row = schemas.ProviderConfigOut.model_validate(raw)
        config = row.config or {}
        modality = config.get("modality") or ("text" if row.kind == "llm" else None)
        if (
            not row.enabled
            or config.get("hidden_from_catalog")
            or modality not in {"text", "image", "video"}
            or row.kind != ("llm" if modality == "text" else "generation")
            or (args.modality and args.modality != modality)
        ):
            continue
        item = {
            "provider": row.provider_name,
            "name": row.display_name or row.provider_name,
            "kind": row.kind,
            "modality": modality,
            "scope": "personal" if config.get("scope") == "personal" else "project",
            "is_default": bool(config.get("is_default")),
        }
        try:
            if modality == "text":
                resolved = resolve_cloud_input(db, ctx, row.provider_name)
                item["input_capabilities"] = resolved["input_contract"]
                item["param_schema"] = {}
            else:
                caps = service._provider_instance(
                    db, ctx.project.id, row.provider_name, user_id=ctx.user.id
                ).capabilities()
                if modality not in caps.modalities:
                    unavailable.append({**item, "reason": "已配置类型与实际适配器能力不一致"})
                    continue
                item["capabilities"] = caps.model_dump(mode="json")
                item["param_schema"] = caps.param_schema
                item["generation_allowed"] = can(ctx.role, "generation.trigger")
                item["is_mock"] = config.get("protocol", row.provider_name) == "mock"
            item["available"] = True
            models.append(item)
        except (AppError, ValueError, KeyError, TypeError):
            # Never echo raw adapter errors: they may contain an address or key.
            unavailable.append({**item, "reason": "本地模型配置或能力声明不可用，请检查模型服务"})
    defaults, suggested = {}, {}
    for modality in ("text", "image", "video"):
        selected = [item for item in models if item["modality"] == modality]
        default = next((item for item in selected if item["is_default"]), None)
        defaults[modality] = default["provider"] if default else None
        suggested[modality] = (default or selected[0])["provider"] if selected else None
    end = args.offset + args.limit
    return {
        "models": models[args.offset : end],
        "total": len(models),
        "next_offset": end if end < len(models) else None,
        "defaults": defaults,
        "suggested": suggested,
        "unavailable": unavailable[args.offset : end],
        "unavailable_total": len(unavailable),
        "note": (
            "provider 可直接用于 media.estimate / media.generate；先估价校验具体参数与参考素材。"
            "suggested 与创作输入框一致：已配置默认优先，否则目录首个可用模型。"
            "目录是本地配置声明，不代表已联网验证；多线路能力是并集，组合可用性以估价为准。"
        ),
    }


def register_tools():
    registry.register(
        registry.Tool(
            "media.models",
            "读取本项目及当前用户的可用文字、图片、视频模型和默认选择、参考限制、参数选项；不联网、不生成。",
            Models,
            group="media",
        ),
        list_models,
    )
    register_contract(
        "media.models",
        ToolPolicy(
            effect="read",
            scope="project",
            idempotency="read_only",
            cancellation="not_applicable",
            recovery="repeat_read",
            output_kind="model_catalog",
        ),
    )
