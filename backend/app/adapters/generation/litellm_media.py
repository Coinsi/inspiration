"""Multi-provider SDK media adapter; persistent jobs remain owned by Inspiration."""

import base64
import uuid
from io import BytesIO

from app.adapters.contracts import (
    Capabilities,
    CostEstimate,
    GenerationProvider,
    GenerationResult,
    JobHandle,
    Output,
    generation_registry,
    reference_error,
)
from app.adapters.generation.reference_transport import image_file
from app.adapters.model_sdk import connection, failure, sdk, single_submission_client
from app.core.errors import CapabilityUnsupported, ProviderError

# Only API-key providers compatible with our channel credential contract are exposed.
# SDK-supported IAM/service-account providers need a separate credential contract.
SDK_PROVIDERS = {
    "text": ("openai", "anthropic", "gemini", "deepseek", "mistral", "groq", "xai", "openrouter"),
    "image": ("openai", "gemini", "fal_ai", "recraft", "black_forest_labs"),
    "video": ("openai", "gemini", "runwayml", "fal_ai"),
}


@generation_registry.register("litellm")
class LiteLLMMediaProvider(GenerationProvider):
    name = "litellm"

    def __init__(self, endpoint=None, token=None, config=None, **_):
        self.endpoint, self.token = endpoint, token
        self.config = config or {}
        self.provider = self.config.get("sdk_provider", "openai")
        self.model = self.config.get("model", "")
        self.modality = self.config.get("modality", "image")

    def _adapter(self, *, edit=False):
        library = sdk()
        from litellm.utils import ProviderConfigManager

        if self.provider not in SDK_PROVIDERS.get(self.modality, ()):
            raise CapabilityUnsupported("当前接入方式不支持所选供应商和模型用途")
        factory = (
            ProviderConfigManager.get_provider_video_config
            if self.modality == "video"
            else ProviderConfigManager.get_provider_image_edit_config
            if edit
            else ProviderConfigManager.get_provider_image_generation_config
        )
        adapter = factory(model=self.model, provider=library.LlmProviders(self.provider))
        if adapter is None:
            raise CapabilityUnsupported("已安装的 SDK 不支持该模型的此项能力")
        return adapter

    def capabilities(self):
        images = min(
            int(self.config.get("max_reference_images", 0)), 1 if self.modality == "video" else 16
        )
        features = set()
        if images:
            self._adapter(edit=True)
            features.update({"reference", "img2img"})
            if self.modality == "video":
                features.add("first_frame")
            elif self.config.get("mask_editing"):
                features.add("inpaint")
        return Capabilities(
            strict_parameters=True,
            modalities={self.modality},
            features=features,
            max_reference_images=images,
            reference_transports={"image": ["multipart"]} if images else {},
            max_video_seconds=self.config.get("max_video_seconds", 10),
            param_schema=self.config.get("param_schema", {}),
        )

    def validate_request(self, req):
        if req.request_type != self.modality:
            raise CapabilityUnsupported("模型用途与请求不一致")
        if req.count < 1 or (self.modality == "video" and req.count != 1):
            raise CapabilityUnsupported("视频任务每次只能生成一个结果")
        error = reference_error(req.references, self.capabilities())
        if error:
            raise CapabilityUnsupported(error)
        if req.mask and (not req.references or not self.config.get("mask_editing")):
            raise CapabilityUnsupported("此模型未开启蒙版编辑，或没有参考图")
        if req.negative or req.seed is not None:
            raise CapabilityUnsupported("此 SDK 接入尚未开放负向提示词和随机种子")
        values = {**req.params, **req.provider_params}
        allowed = {"duration", "size"} if self.modality == "video" else {"size", "quality"}
        if set(values) - allowed:
            raise CapabilityUnsupported("请求包含未开放的 SDK 参数")
        adapter = self._adapter(edit=bool(req.references))
        supported = set(adapter.get_supported_openai_params(self.model))
        requested = {"seconds" if key == "duration" else key for key in values}
        if req.mask:
            requested.add("mask")
        if req.references and self.modality == "video":
            requested.add("input_reference")
        if requested - supported:
            raise CapabilityUnsupported("已安装的 SDK 不支持本次选择的参数或参考素材")

    def estimate_cost(self, req):
        self.validate_request(req)
        return CostEstimate(points=0, detail={"pricing": "unknown"})

    def submit(self, req):
        self.validate_request(req)
        params = connection(self.endpoint, self.token, self.provider)
        params.update(model=self.model, prompt=req.prompt)
        params.update({**req.params, **req.provider_params})
        images = [BytesIO(image_file(ref)[1]) for ref in req.references]
        try:
            if self.modality == "video":
                if "duration" in params:
                    params["seconds"] = str(params.pop("duration"))
                if images:
                    params["input_reference"] = images[0]
                result = sdk().video_generation(**params)
                if not result.id:
                    raise ProviderError("视频服务未返回任务标识，提交结果待核实")
                # SDK IDs include provider/model needed to resume after process restart.
                return JobHandle(external_job_id=result.id)
            params["n"] = req.count
            if images:
                params["image"] = images if len(images) > 1 else images[0]
                if req.mask:
                    params["mask"] = image_file(req.mask)[1]
                result = sdk().image_edit(**params)
            else:
                if self.provider == "openai":
                    # LiteLLM 1.106.3 drops max_retries when building image optional
                    # params and overwrites even a supplied client's retry setting.
                    # Fence the request-local transport to prevent paid replays.
                    with single_submission_client(self.endpoint, self.token) as client:
                        result = sdk().image_generation(client=client, **params)
                else:
                    result = sdk().image_generation(**params)
            return JobHandle(
                external_job_id="sdk-image-" + uuid.uuid4().hex, raw=result.model_dump(mode="json")
            )
        except ProviderError:
            raise
        except Exception as exc:
            if getattr(exc, "status_code", None) in (401, 403, 404, 429):
                from app.modules.generation.model_routing import SubmissionRejected

                raise SubmissionRejected(failure(exc, "生成服务拒绝提交").message) from None
            raise failure(exc, "生成提交") from None

    def poll(self, handle):
        if self.modality == "image":
            outputs = []
            for item in handle.raw.get("data", []):
                if item.get("b64_json"):
                    outputs.append(Output(data=base64.b64decode(item["b64_json"], validate=True)))
                elif item.get("url"):
                    outputs.append(Output(url=item["url"]))
            if not outputs:
                raise ProviderError("图片服务没有返回可用结果")
            return GenerationResult(
                status="succeeded", outputs=outputs, cost_raw=handle.raw.get("usage")
            )
        params = connection(self.endpoint, self.token, self.provider)
        try:
            result = sdk().video_status(video_id=handle.external_job_id, **params)
            if result.status in ("queued", "in_progress", "processing"):
                return GenerationResult(status="running")
            if result.status in ("failed", "cancelled"):
                return GenerationResult(
                    status="failed", error="上游视频任务失败，请检查渠道任务记录"
                )
            if result.status != "completed":
                raise ProviderError("视频服务返回未知状态，保留任务标识以便恢复")
            content = sdk().video_content(video_id=handle.external_job_id, **params)
            if not isinstance(content, bytes) or not content or len(content) > 256 * 1024 * 1024:
                raise ProviderError("视频结果为空或超过 256 MB 接收限制")
            return GenerationResult(
                status="succeeded", outputs=[Output(data=content, type="video")]
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise failure(exc, "视频结果读取") from None
