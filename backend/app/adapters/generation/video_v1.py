"""Explicit JSON video.v1 text-to-video protocol; never infer it from a model name.

Reference media require public URLs on this protocol. Our local reference objects
currently carry data URIs, so do not advertise reference support here.
"""

from app.adapters.contracts import Capabilities, JobHandle, generation_registry
from app.adapters.generation.openai_video import OpenAIVideoProvider
from app.core.errors import CapabilityUnsupported, ProviderError


@generation_registry.register("video_v1")
class VideoV1Provider(OpenAIVideoProvider):
    name = "video_v1"
    content_query = "?download=1"
    content_redirects = True

    def capabilities(self):
        return Capabilities(
            modalities={"video"},
            max_reference_images=0,
            max_video_seconds=self.config.get("max_video_seconds", 30),
            param_schema=self.config.get("param_schema", {}),
            strict_parameters=True,
        )

    def validate_request(self, req):
        if req.request_type != "video" or req.count != 1:
            raise CapabilityUnsupported("Video v1 每次生成一个视频，请将数量设为 1")
        if req.references or req.mask:
            raise CapabilityUnsupported(
                "Video v1 当前接入支持文生视频；参考素材需要公网地址，尚未接通"
            )
        values = req.provider_params
        if set(values) - {"duration", "resolution", "aspect_ratio"}:
            raise CapabilityUnsupported("Video v1 支持时长、分辨率和画面比例参数")
        if "duration" not in values:
            raise CapabilityUnsupported("请明确选择视频时长，避免使用上游默认时长产生额外费用")
        for key, value in values.items():
            if value not in self.config.get("param_schema", {}).get(key, {}).get("enum", []):
                raise CapabilityUnsupported(f"当前模型未声明支持该参数值：{key}")

    def estimate_cost(self, req):
        self.validate_request(req)
        return super().estimate_cost(req)

    def submit(self, req):
        self.validate_request(req)
        fields = {
            "version": "video.v1",
            "model": self.config["model"],
            "operation": "text_to_video",
            "prompt": req.prompt,
            "duration_seconds": req.provider_params["duration"],
        }
        for name in ("resolution", "aspect_ratio"):
            if name in req.provider_params:
                fields[name] = req.provider_params[name]
        result = self._request("POST", "/videos", json=fields)
        if not isinstance(result.get("id"), str) or not result["id"]:
            raise ProviderError("视频服务未返回任务编号；请检查上游记录后再决定是否重试")
        return JobHandle(external_job_id=result["id"])
