"""即梦(Jimeng)生成供应商适配器(决策 D1:图像 + 视频)。

说明:即梦能力通常经火山引擎视觉服务开放。此处实现统一抽象与任务生命周期骨架;
具体请求体/签名/轮询字段需按所用即梦/火山引擎 API 文档对接(标注 TODO 处)。
凭据与端点来自 provider_config(加密存储),由生成服务注入构造参数。
"""
import json
import urllib.request

from app.adapters.contracts import (
    Capabilities,
    CostEstimate,
    GenerationProvider,
    GenerationRequest,
    GenerationResult,
    JobHandle,
    Output,
    generation_registry,
)
from app.core.errors import ProviderError


@generation_registry.register("jimeng")
class JimengProvider(GenerationProvider):
    name = "jimeng"

    def __init__(self, endpoint: str | None = None, token: str | None = None, config: dict | None = None, **_):
        self.endpoint = endpoint
        self.token = token
        self.config = config or {}

    def _require(self):
        if not self.endpoint or not self.token:
            raise ProviderError("即梦未配置端点/密钥(请在项目供应商配置中设置)")

    def capabilities(self) -> Capabilities:
        return Capabilities(
            modalities={"image", "video"},
            features={"reference", "img2img"},
            max_reference_images=int(self.config.get("max_reference_images", 4)),
            max_video_seconds=float(self.config.get("max_video_seconds", 10)),
            param_schema=self.config.get("param_schema", {}),
        )

    def estimate_cost(self, req: GenerationRequest) -> CostEstimate:
        rate = self.config.get("rate", {"image": 20, "video": 200})
        unit = rate.get(req.request_type, 20)
        return CostEstimate(points=unit * req.count, detail={"unit": unit})

    def submit(self, req: GenerationRequest) -> JobHandle:
        self._require()
        # TODO: 按即梦/火山引擎 API 组装请求体与鉴权签名
        body = {
            "prompt": req.prompt,
            "negative_prompt": req.negative,
            "type": req.request_type,
            "n": req.count,
            **req.provider_params,
        }
        # 参考图(图生图/参考图):服务层已物化为 base64 data URI
        ref_urls = [r.data_url for r in req.references if r.data_url]
        if ref_urls:
            body["reference_images"] = ref_urls
        request = urllib.request.Request(
            f"{self.endpoint.rstrip('/')}/submit",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:  # noqa: S310
                data = json.loads(resp.read())
            return JobHandle(external_job_id=str(data.get("task_id", "")), raw=data)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"即梦提交失败:{exc}") from exc

    def poll(self, handle: JobHandle) -> GenerationResult:
        self._require()
        # TODO: 按即梦 API 查询任务状态并下载产物
        try:
            request = urllib.request.Request(
                f"{self.endpoint.rstrip('/')}/status?task_id={handle.external_job_id}",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            with urllib.request.urlopen(request, timeout=60) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"即梦轮询失败:{exc}") from exc

        status = data.get("status")
        if status in ("running", "pending"):
            return GenerationResult(status="running")
        if status == "succeeded":
            outputs = [
                Output(url=u, type=data.get("type", "image")) for u in data.get("outputs", [])
            ]
            return GenerationResult(status="succeeded", outputs=outputs, cost_raw=data.get("usage"))
        return GenerationResult(status="failed", error=data.get("error", "unknown"))
