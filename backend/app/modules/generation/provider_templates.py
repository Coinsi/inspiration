"""Editable provider directory, restricted to implemented connection protocols."""

from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text

from app.core import audit
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.errors import Conflict
from app.core.platform_permissions import platform_admin
from app.models.generation import ProviderTemplate
from app.modules.generation.channels import ChannelIn, ModelIn


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modality: Literal["text", "image", "video"]
    protocol: str
    sdk_provider: str = "openai"

    @model_validator(mode="after")
    def supported(self):
        ModelIn(
            name="validation",
            model="validation",
            modality=self.modality,
            protocol=self.protocol,
            sdk_provider=self.sdk_provider,
            parameters={"duration": [5]} if self.protocol == "video_v1" else {},
        )
        return self


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    icon: str = Field(default="", max_length=60, pattern=r"^[a-zA-Z0-9_-]*$")
    endpoint: str = Field(max_length=512)
    key_url: str = Field(default="", max_length=512)
    note: str = Field(default="", max_length=2000)
    discovery_protocol: Literal["openai", "anthropic", "gemini", "openrouter", "manual"] = "openai"
    enabled: bool = True
    routes: list[Route] = Field(min_length=1, max_length=3)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def valid(self):
        self.endpoint = ChannelIn(name=self.name, endpoint=self.endpoint).endpoint
        if self.key_url:
            u = urlsplit(self.key_url)
            if u.scheme != "https" or not u.hostname or u.username or u.password:
                raise ValueError("密钥指引需为 HTTPS 网页")
        if len({r.modality for r in self.routes}) != len(self.routes):
            raise ValueError("每种用途只能配置一个默认接入协议")
        return self


def seed(name, icon, endpoint, key_url, routes, note="", *, discovery=None):
    return dict(
        name=name,
        icon=icon,
        endpoint=endpoint,
        key_url=key_url,
        note=note,
        discovery_protocol=discovery
        or ("anthropic" if icon == "claude" else "gemini" if icon == "gemini" else "openai"),
        enabled=True,
        routes=[dict(modality=m, protocol=p, sdk_provider=s) for m, p, s in routes],
    )


BUILTINS = {
    "openai": seed(
        "OpenAI",
        "openai",
        "https://api.openai.com/v1",
        "https://platform.openai.com/api-keys",
        [
            ("text", "cloud_llm", "openai"),
            ("image", "gpt_image", "openai"),
            ("video", "openai_video", "openai"),
        ],
        "用 OpenAI 平台项目的 API Key 连接。图片和视频分别添加模型，可共用这份密钥。",
    ),
    "anthropic": seed(
        "Anthropic",
        "claude",
        "https://api.anthropic.com",
        "https://console.anthropic.com/",
        [("text", "cloud_llm", "anthropic")],
    ),
    "google": seed(
        "Google Gemini",
        "gemini",
        "https://generativelanguage.googleapis.com",
        "https://aistudio.google.com/apikey",
        [
            ("text", "cloud_llm", "gemini"),
            ("image", "litellm", "gemini"),
            ("video", "litellm", "gemini"),
        ],
        "从 Google AI Studio 获取 API Key。图片和视频可共用连接，分别添加对应的生成模型。",
    ),
    "runway": seed(
        "Runway",
        "runway",
        "https://api.dev.runwayml.com/v1",
        "https://dev.runwayml.com/",
        [("video", "litellm", "runwayml")],
        "从开发者控制台获取 API Key，填写已开通的视频模型 ID。",
        discovery="manual",
    ),
    "recraft": seed(
        "Recraft",
        "recraft",
        "https://external.api.recraft.ai",
        "https://www.recraft.ai/",
        [("image", "litellm", "recraft")],
        "使用 Recraft API 密钥，模型 ID 可从供应商文档复制。",
        discovery="manual",
    ),
    "fal-image": seed(
        "fal.ai",
        "fal",
        "https://fal.run",
        "https://fal.ai/dashboard/keys",
        [("image", "litellm", "fal_ai")],
        "从 fal 模型页面复制图片生成的模型 ID。图片与视频队列使用不同地址。",
        discovery="manual",
    ),
    "fal-video": seed(
        "fal.ai",
        "fal",
        "https://queue.fal.run",
        "https://fal.ai/dashboard/keys",
        [("video", "litellm", "fal_ai")],
        "使用视频模型页面的模型 ID，保留队列服务地址。",
        discovery="manual",
    ),
    "bfl": seed(
        "Black Forest Labs",
        "bfl",
        "https://api.bfl.ai",
        "https://dashboard.bfl.ai/",
        [("image", "litellm", "black_forest_labs"), ("video", "bfl_video", "openai")],
        "图片填写 FLUX 图片模型 ID。视频可填写 flux-3-video，支持文字或首尾帧输入。",
        discovery="manual",
    ),
    "deepseek": seed(
        "DeepSeek",
        "deepseek",
        "https://api.deepseek.com/v1",
        "https://platform.deepseek.com/api_keys",
        [("text", "cloud_llm", "deepseek")],
    ),
    "ark": seed(
        "火山方舟",
        "volcengine",
        "https://ark.cn-beijing.volces.com/api/v3",
        "https://console.volcengine.com/ark",
        [
            ("text", "cloud_llm", "openai"),
            ("image", "ark_image", "openai"),
            ("video", "ark_video", "openai"),
        ],
        "使用方舟 API Key，并在控制台开通模型。即梦账号和 AK/SK 不能替代此密钥。",
    ),
    "byteplus": seed(
        "BytePlus ModelArk",
        "byteplus",
        "https://ark.ap-southeast.bytepluses.com/api/v3",
        "https://console.byteplus.com/",
        [
            ("text", "cloud_llm", "openai"),
            ("image", "ark_image", "openai"),
            ("video", "ark_video", "openai"),
        ],
        "使用 BytePlus ModelArk 国际站的密钥，填写已开通的模型 ID 或推理接入点 ID。",
    ),
    "minimax": seed(
        "MiniMax",
        "minimax",
        "https://api.minimax.cn/v1",
        "https://platform.minimax.cn/",
        [
            ("text", "cloud_llm", "openai"),
            ("image", "minimax_image", "openai"),
            ("video", "minimax_video", "openai"),
        ],
        "可拉取官方目录，也可手动填写已开通的模型 ID。视频生成与文字理解分别配置。",
    ),
    "openrouter": seed(
        "OpenRouter",
        "openrouter",
        "https://openrouter.ai/api/v1",
        "https://openrouter.ai/settings/keys",
        [
            ("text", "cloud_llm", "openrouter"),
            ("image", "litellm", "openrouter"),
            ("video", "openrouter_video", "openai"),
        ],
        "同一密钥可用于图片和视频。请从对应用途的目录选择含作者前缀的模型 ID。",
        discovery="openrouter",
    ),
    "qwen": seed(
        "阿里云百炼",
        "bailian",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://bailian.console.aliyun.com/",
        [("text", "cloud_llm", "openai")],
    ),
    "zhipu": seed(
        "智谱 / Zhipu",
        "zhipu",
        "https://open.bigmodel.cn/api/paas/v4",
        "https://open.bigmodel.cn/",
        [
            ("text", "cloud_llm", "openai"),
            ("image", "zhipu_image", "openai"),
            ("video", "zhipu_video", "openai"),
        ],
        "图片使用 CogView 系列，视频使用 CogVideoX 系列。模型与权限以开放平台控制台为准。",
        discovery="manual",
    ),
    "xai": seed(
        "xAI",
        "xai",
        "https://api.x.ai/v1",
        "https://console.x.ai/",
        [
            ("text", "cloud_llm", "xai"),
            ("image", "xai_image", "openai"),
            ("video", "xai_video", "openai"),
        ],
        "使用 xAI Console 密钥，分别添加 Grok Imagine 图片模型和视频模型。",
    ),
    "wavespeed": seed(
        "WaveSpeed",
        "wavespeed",
        "https://api.wavespeed.ai/api/v3",
        "https://wavespeed.ai/accesskey",
        [("image", "wavespeed_image", "openai")],
        "从图片模型页复制完整模型 ID，保留服务商前缀。此入口用于文字生成图片。",
        discovery="manual",
    ),
    "kling": seed(
        "可灵 / Kling",
        "kling",
        "https://api-beijing.klingai.com/v1",
        "https://klingai.com/dev",
        [("image", "kling_image", "openai"), ("video", "kling_video", "openai")],
        "在开放平台创建 Access Key 和 Secret Key。选择账号已开通的模型版本，图片与视频共用凭据。",
        discovery="manual",
    ),
    "bailian-media": seed(
        "阿里云百炼",
        "bailian",
        "https://dashscope.aliyuncs.com/api/v1",
        "https://bailian.console.aliyun.com/",
        [("image", "litellm", "dashscope"), ("video", "bailian_video", "openai")],
        "使用百炼原生媒体地址，按用途填写模型 ID。密钥与地址须属于同一地域。",
        discovery="manual",
    ),
    "vidu": seed(
        "Vidu / 生数",
        "vidu",
        "https://api.vidu.cn/ent/v2",
        "https://platform.vidu.cn/",
        [("video", "vidu_video", "openai")],
        "国内站与国际站分别发放密钥。国际站使用 https://api.vidu.com/ent/v2。可用时长与分辨率取决于所选模型。",
        discovery="manual",
    ),
    "heygen": seed(
        "HeyGen",
        "heygen",
        "https://api.heygen.com/v3",
        "https://platform.heygen.com/api-keys",
        [("video", "heygen_video", "openai")],
        "使用含视频读写权限的 API Key，填写 heygen-video-1，可从文字或首帧图片生成视频。",
        discovery="manual",
    ),
    "openai-compatible-video": seed(
        "OpenAI 兼容视频",
        "openai",
        "",
        "",
        [("video", "openai_video", "openai")],
        "填写服务商提供的 Base URL 与密钥。该入口使用 OpenAI Videos 的创建、查询和下载接口。",
    ),
}


def listing(db, all_rows=False):
    order = (
        "openai",
        "google",
        "anthropic",
        "deepseek",
        "minimax",
        "wavespeed",
        "openrouter",
        "byteplus",
        "xai",
        "ark",
        "kling",
        "zhipu",
        "qwen",
        "bailian-media",
        "heygen",
        "bfl",
        "vidu",
        "openai-compatible-video",
    )
    values = {
        key: {"id": key, **BUILTINS[key], "revision": 0}
        for key in dict.fromkeys((*order, *BUILTINS))
    }
    for row in db.scalars(select(ProviderTemplate)):
        values[row.id] = {"id": row.id, **row.definition, "revision": row.revision}
    return [v for v in values.values() if all_rows or v["enabled"]]


router = APIRouter(tags=["provider-directory"])
DB = Depends(get_db, scope="function")
USER = Depends(get_current_user)
ADMIN = Depends(platform_admin)


@router.get("/model-provider-templates")
def catalog(user=USER, db=DB):
    return listing(db)


@router.get("/admin/model-provider-templates")
def admin_catalog(user=ADMIN, db=DB):
    return listing(db, True)


@router.put("/admin/model-provider-templates/{template_id}")
def save(template_id: str, data: TemplateIn, user=ADMIN, db=DB):
    import re

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", template_id):
        raise Conflict("模板标识仅支持小写字母、数字、横线和下划线")
    db.execute(text("SELECT pg_advisory_xact_lock(18423, 60)"))
    row = db.get(ProviderTemplate, template_id)
    if data.revision != (row.revision if row else 0):
        raise Conflict("模板已更新，请重新读取")
    if row is None:
        row = ProviderTemplate(id=template_id, revision=0)
        db.add(row)
    row.definition = data.model_dump(exclude={"revision"})
    row.revision += 1
    audit.record(
        db,
        action="provider_template.save",
        user_id=user.id,
        detail={"template_id": template_id, "revision": row.revision},
    )
    db.flush()
    return {"id": row.id, **row.definition, "revision": row.revision}
