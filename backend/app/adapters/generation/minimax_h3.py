"""MiniMax H3 video generation and polling via the asynchronous V2 API."""

import json
from urllib.parse import quote

from app.adapters.contracts import GenerationResult, JobHandle, generation_registry
from app.adapters.generation.native_media import NativeMedia
from app.core.errors import CapabilityUnsupported, ProviderError
from app.modules.generation.minimax_models import H3_PROTOCOL, video_profile


@generation_registry.register(H3_PROTOCOL)
class MiniMaxH3VideoProvider(NativeMedia):
    name = H3_PROTOCOL
    max_refs = 2
    supports_single_last_frame = True
    allowed_parameters = {"duration", "resolution", "aspect_ratio"}

    def __init__(self, endpoint=None, token=None, config=None, **kwargs):
        config = video_profile(
            {**(config or {}), "modality": "video", "protocol": self.name}, endpoint
        )
        super().__init__(endpoint, token, config, **kwargs)
        # Keep the configured host and any gateway prefix when replacing the API version.
        if self.endpoint:
            if self.endpoint.endswith(("/v1", "/v2")):
                self.endpoint = self.endpoint.rsplit("/", 1)[0]
            self.endpoint += "/v2"

    def capabilities(self):
        caps = super().capabilities()
        caps.aspect_ratio_from_image = caps.max_reference_images > 0
        return caps

    def validate_request(self, req):
        super().validate_request(req)
        if not req.prompt.strip():
            raise CapabilityUnsupported("H3 视频需要非空的画面描述")
        if any(ref.kind != "image" for ref in req.references):
            raise CapabilityUnsupported("当前 H3 视频接入支持首帧和尾帧图片")
        schema = self.config["param_schema"]
        required = ("duration", "resolution") if req.references else self.allowed_parameters
        if any(not schema[key]["enum"] for key in required):
            raise CapabilityUnsupported(
                "已配置的时长、清晰度或比例与 H3 模型不匹配，请编辑模型参数"
            )
        if req.references and req.provider_params.get("aspect_ratio"):
            raise CapabilityUnsupported("首尾帧视频沿用参考图的比例，请将画面比例设为模型默认")

    def submit(self, req):
        self.validate_request(req)
        options = req.provider_params
        schema = self.config["param_schema"]
        content = [{"type": "text", "text": req.prompt}]
        for ref in req.references:
            url = self._image(ref)
            if len(url.partition(",")[2]) > 4 * (30 * 1024 * 1024 // 3):
                raise CapabilityUnsupported("H3 的每张参考图最多 30 MB")
            content.append(
                {
                    "type": "image_url",
                    "role": "last_frame" if ref.role == "last_frame" else "first_frame",
                    "image_url": {"url": url},
                }
            )
        body = {
            "model": self.config["model"],
            "content": content,
            "duration": options.get("duration", min(schema["duration"]["enum"])),
            "resolution": options.get("resolution", schema["resolution"]["enum"][0]),
            # Text-only H3 requires an explicit ratio; frame-based H3 infers it from the image.
            "ratio": "adaptive"
            if req.references
            else options.get("aspect_ratio", schema["aspect_ratio"]["enum"][0]),
        }
        if req.references and len(json.dumps(body, ensure_ascii=False).encode()) > 64 * 1024**2:
            raise CapabilityUnsupported("参考图片合计超过 H3 请求上限 64 MB，请缩小图片后重试")
        data = self._request("POST", "/video_generation", json=body)
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ProviderError("MiniMax H3 未返回任务编号，请先核对供应商任务记录")
        return JobHandle(external_job_id=task_id)

    def poll(self, handle):
        path = "/query/video_generation/" + quote(handle.external_job_id, safe="")
        data = self._request("GET", path)
        task = data.get("task")
        if not isinstance(task, dict) or task.get("id") != handle.external_job_id:
            raise ProviderError("MiniMax H3 返回的任务与提交记录不符，请继续查询原任务")
        status = task.get("status")
        if status in ("queued", "running"):
            return GenerationResult(status="running")
        if status in ("failed", "cancelled"):
            message = "已取消" if status == "cancelled" else "生成失败"
            return GenerationResult(
                status="failed", error=f"MiniMax H3 任务{message}，可在供应商任务记录查看详情"
            )
        if status != "succeeded":
            raise ProviderError("MiniMax H3 任务状态暂不可用，请继续查询原任务")
        content = task.get("content")
        url = content.get("url") if isinstance(content, dict) else None
        return GenerationResult(status="succeeded", outputs=[self._output(url)])
