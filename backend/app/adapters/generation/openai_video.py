"""OpenAI-compatible multipart /videos task protocol. No vendor-name inference."""

import base64
import re
from urllib.parse import quote

import httpx

from app.adapters.contracts import (
    Capabilities,
    CostEstimate,
    GenerationProvider,
    GenerationResult,
    JobHandle,
    Output,
    generation_registry,
)
from app.core.errors import CapabilityUnsupported, ProviderError


@generation_registry.register("openai_video")
class OpenAIVideoProvider(GenerationProvider):
    name = "openai_video"
    content_query = ""
    content_redirects = False

    def __init__(self, endpoint=None, token=None, config=None, **_):
        self.endpoint = (endpoint or "").rstrip("/")
        self.token = token
        self.config = config or {}

    def capabilities(self):
        refs = bool(self.config.get("max_reference_images"))
        return Capabilities(
            modalities={"video"},
            features={"reference", "img2img", "first_frame"} if refs else set(),
            max_reference_images=1 if refs else 0,
            max_video_seconds=self.config.get("max_video_seconds", 12),
            param_schema=self.config.get("param_schema", {}),
        )

    def estimate_cost(self, req):
        if req.request_type != "video" or req.count != 1:
            raise CapabilityUnsupported("Videos 协议每次创建一个视频，请将数量设为1")
        return CostEstimate(
            points=0, detail={"pricing": "unknown", "message": "未配置计价，实际费用以上游账单为准"}
        )

    def _request(self, method, path, **kwargs):
        if not self.endpoint or not self.token:
            raise ProviderError("请先配置渠道地址和密钥")
        try:
            with httpx.Client(timeout=120, follow_redirects=False) as client:
                response = client.request(
                    method,
                    self.endpoint + path,
                    headers={"Authorization": "Bearer " + self.token},
                    **kwargs,
                )
            if not response.is_success:
                try:
                    detail = self._error_message(response.json())
                except ValueError:
                    detail = ""
                suffix = f"：{detail}" if detail else ""
                if method == "POST" and response.status_code in (401, 403, 404, 429):
                    from app.modules.generation.model_routing import SubmissionRejected

                    raise SubmissionRejected(
                        f"视频服务拒绝提交（HTTP {response.status_code}），未创建任务{suffix}"
                    )
                raise ProviderError(f"视频服务请求失败（HTTP {response.status_code}）{suffix}")
            return response.json()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("视频服务连接或返回格式异常") from exc

    def _error_message(self, data):
        """Keep actionable vendor errors without persisting credentials or signed URLs."""
        if not isinstance(data, dict):
            return ""
        error = data.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        if not isinstance(message, str):
            return ""
        if self.token:
            message = message.replace(self.token, "[已隐藏密钥]")
        message = re.sub(r"https?://\S+", "[服务地址]", message)
        message = re.sub(r"(?i)bearer\s+\S+|sk-[\w-]+", "[已隐藏密钥]", message)
        return " ".join(message.split())[:500]

    def submit(self, req):
        self.estimate_cost(req)
        values = dict(req.provider_params)
        if set(values) - {"duration", "size"}:
            raise CapabilityUnsupported("Videos 协议仅支持时长与画面尺寸参数")
        fields = {"model": self.config["model"], "prompt": req.prompt}
        if "duration" in values:
            fields["seconds"] = str(values["duration"])
        if "size" in values:
            fields["size"] = str(values["size"])
        files = [(k, (None, v)) for k, v in fields.items()]
        if req.references:
            if len(req.references) != 1 or not self.config.get("max_reference_images"):
                raise CapabilityUnsupported("当前模型只支持已声明的一张参考图")
            head, data = req.references[0].data_url.split(",", 1)
            mime = head.split(":", 1)[1].split(";")[0]
            files.append(("input_reference", ("reference.png", base64.b64decode(data), mime)))
        result = self._request("POST", "/videos", files=files)
        if not isinstance(result.get("id"), str) or not result["id"]:
            raise ProviderError("视频服务未返回任务编号；请检查上游记录后再决定是否重试")
        return JobHandle(external_job_id=result["id"])

    def poll(self, handle):
        path = "/videos/" + quote(handle.external_job_id, safe="")
        data = self._request("GET", path)
        status = data.get("status")
        if status in ("queued", "pending", "in_progress", "running", "processing"):
            return GenerationResult(status="running")
        if status in ("failed", "canceled", "cancelled"):
            return GenerationResult(
                status="failed",
                error=self._error_message(data) or "上游视频任务失败或已取消，请查看服务商任务记录",
            )
        if status not in ("completed", "succeeded"):
            # Unknown state is a protocol/query problem, not proof of a failed generation.
            # Raising preserves the accepted handle so retry only polls this same task.
            raise ProviderError(
                "视频服务返回了无法识别的任务状态；请核对上游记录，可继续查询原任务"
            )
        return self._download(path)

    def _download(self, path):
        try:
            with httpx.Client(
                timeout=120, follow_redirects=self.content_redirects, max_redirects=5
            ) as client:
                with client.stream(
                    "GET",
                    self.endpoint + path + "/content" + self.content_query,
                    headers={"Authorization": "Bearer " + self.token},
                ) as response:
                    if not response.is_success:
                        raise ProviderError(
                            f"视频已完成，但下载失败（HTTP {response.status_code}）"
                        )
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 256 * 1024 * 1024:
                            raise ProviderError("视频超过当前单次接收上限256MB")
            return GenerationResult(
                status="succeeded",
                outputs=[
                    Output(data=bytes(raw), type="video", meta={"model": self.config["model"]})
                ],
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("视频已完成，但下载中断，请检查上游任务") from exc

    # No standard upstream cancellation contract. Local cancellation does not promise a refund.
