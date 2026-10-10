"""Native Ark and MiniMax contracts; task polling never resubmits generation."""

import uuid
from urllib.parse import quote, urlsplit

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


class NativeMedia(GenerationProvider):
    modality = "video"
    allowed_parameters = set()
    max_refs = 1

    def __init__(self, endpoint=None, token=None, config=None, **_):
        self.endpoint = (endpoint or "").rstrip("/")
        self.token = token
        self.config = config or {}

    def capabilities(self):
        refs = min(self.max_refs, self.config.get("max_reference_images", 0))
        features = {"reference", "img2img"} if refs else set()
        if refs and self.modality == "video":
            features.add("first_frame")
        if refs > 1 and self.modality == "video":
            features.add("last_frame")
        return Capabilities(
            modalities={self.modality},
            features=features,
            max_reference_images=refs,
            max_video_seconds=self.config.get("max_video_seconds", 12)
            if self.modality == "video"
            else None,
            param_schema=self.config.get("param_schema", {}),
            strict_parameters=True,
        )

    def validate_request(self, req):
        if req.request_type != self.modality or req.count != 1:
            raise CapabilityUnsupported("此接口每次生成一个结果，请将数量设为 1")
        if req.mask:
            raise CapabilityUnsupported("此接口不支持蒙版，请使用参考图编辑")
        if set(req.provider_params) - self.allowed_parameters:
            raise CapabilityUnsupported("当前模型不支持所选生成参数")
        caps = self.capabilities()
        if len(req.references) > caps.max_reference_images:
            raise CapabilityUnsupported("参考图数量超出当前模型上限")
        for key, value in req.provider_params.items():
            if value not in caps.param_schema.get(key, {}).get("enum", []):
                raise CapabilityUnsupported("当前模型不支持所选参数值，请重新选择")
        if self.modality == "video":
            roles = [r.role for r in req.references]
            if any(r not in ("ref", "first_frame", "last_frame") for r in roles):
                raise CapabilityUnsupported("当前视频接口只支持首帧或首尾帧图片")
            if len(roles) == 1 and roles[0] == "last_frame":
                raise CapabilityUnsupported("请先添加首帧图片，再使用尾帧")
            if len(roles) == 2 and sorted(roles) != ["first_frame", "last_frame"]:
                raise CapabilityUnsupported("两张参考图需要分别指定为首帧和尾帧")

    def estimate_cost(self, req):
        self.validate_request(req)
        return CostEstimate(
            points=0,
            detail={"pricing": "unknown", "message": "未配置内部积分，实际费用以上游账单为准"},
        )

    def _request(self, method, path, **kwargs):
        if not self.endpoint or not self.token:
            raise ProviderError("请先填写服务密钥")
        try:
            with httpx.Client(timeout=180, follow_redirects=False) as client:
                with client.stream(
                    method,
                    self.endpoint + path,
                    headers={"Authorization": "Bearer " + self.token},
                    **kwargs,
                ) as response:
                    if not response.is_success:
                        messages = {
                            401: "密钥无效，请更换服务密钥",
                            403: "此密钥没有模型权限，请到供应商开通模型",
                            404: "未找到模型或接口，请核对模型名称和服务地址",
                            429: "服务额度不足或请求过于频繁，请检查余额并稍后再试",
                        }
                        # Preserve a bounded vendor error code, never echo raw bodies or keys.
                        import json
                        import re

                        raw = bytearray()
                        for chunk in response.iter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 65536:
                                break
                        code = ""
                        try:
                            err = json.loads(raw).get("error", {})
                            candidate = err.get("code", "") if isinstance(err, dict) else ""
                            if isinstance(candidate, str) and re.fullmatch(
                                r"[A-Za-z][A-Za-z0-9_.-]{0,95}", candidate
                            ):
                                code = candidate
                        except (ValueError, AttributeError):
                            pass
                        message = messages.get(
                            response.status_code,
                            f"服务暂时异常（HTTP {response.status_code}），请稍后查询任务",
                        )
                        known = {
                            "ModelNotOpen": "尚未开通此模型，请在方舟控制台的模型开通页面启用",
                            "ModelNotOpenError": "尚未开通此模型，请在方舟控制台的模型开通页面启用",
                            "InvalidApiKey": "密钥无效，请核对方舟 API Key",
                            "AuthenticationError": "密钥鉴权失败，请核对方舟 API Key",
                            "InvalidEndpointOrModel.NotFound": (
                                "模型不存在或当前账号无权访问，请核对模型开通状态和模型 ID"
                            ),
                            "AccountOverdueError": "供应商账户欠费，请检查账户余额",
                        }
                        message = known.get(code, message)
                        if code:
                            message += f"（{code}）"
                        if method == "POST" and response.status_code in (
                            400,
                            401,
                            403,
                            404,
                            422,
                            429,
                        ):
                            from app.modules.generation.model_routing import SubmissionRejected

                            raise SubmissionRejected(message)
                        raise ProviderError(message)
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise ProviderError("服务返回的数据超过接收上限")
                    import json

                    data = json.loads(body)
                    if not isinstance(data, dict):
                        raise ValueError("Expected object")
                    return data
        except ProviderError:
            raise
        except Exception:
            # Do not expose upstream bodies, request URLs, prompts or credentials.
            raise ProviderError("服务连接或返回格式异常；若已提交，请先查询原任务") from None

    def _image(self, ref):
        if not ref.data_url or not ref.data_url.startswith("data:image/"):
            raise CapabilityUnsupported("参考图尚未准备好，请重新选择图片")
        return ref.data_url

    def _output(self, url):
        if not isinstance(url, str):
            raise ProviderError("任务已完成，但服务未返回结果地址；可继续查询原任务")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ProviderError("服务返回了无效的结果地址")
        return Output(url=url, type=self.modality, meta={"model": self.config["model"]})


@generation_registry.register("ark_image")
class ArkImageProvider(NativeMedia):
    name = "ark_image"
    modality = "image"
    max_refs = 14
    allowed_parameters = {"size"}

    def submit(self, req):
        self.validate_request(req)
        body = {
            "model": self.config["model"],
            "prompt": req.prompt,
            "response_format": "url",
            "stream": False,
            "sequential_image_generation": "disabled",
            **req.provider_params,
        }
        if req.references:
            body["image"] = [self._image(r) for r in req.references]
        data = self._request("POST", "/images/generations", json=body)
        items = data.get("data")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ProviderError("图片服务未返回完整结果，请核对上游记录")
        output = self._output(items[0].get("url"))
        return JobHandle(external_job_id="sync-" + uuid.uuid4().hex, raw={"url": output.url})

    def poll(self, handle):
        return GenerationResult(status="succeeded", outputs=[self._output(handle.raw.get("url"))])


@generation_registry.register("ark_video")
class ArkVideoProvider(NativeMedia):
    name = "ark_video"
    max_refs = 2
    allowed_parameters = {"duration", "resolution", "aspect_ratio"}

    def submit(self, req):
        self.validate_request(req)
        content = [{"type": "text", "text": req.prompt}]
        for ref in req.references:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._image(ref)},
                    "role": "first_frame" if ref.role == "ref" else ref.role,
                }
            )
        body = {"model": self.config["model"], "content": content}
        for key, value in req.provider_params.items():
            body["ratio" if key == "aspect_ratio" else key] = value
        data = self._request("POST", "/contents/generations/tasks", json=body)
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise ProviderError("服务未返回任务编号，请先核对上游记录")
        return JobHandle(external_job_id=data["id"])

    def poll(self, handle):
        data = self._request(
            "GET", "/contents/generations/tasks/" + quote(handle.external_job_id, safe="")
        )
        status = data.get("status")
        if status in ("queued", "running"):
            return GenerationResult(status="running")
        if status in ("failed", "cancelled", "expired"):
            return GenerationResult(
                status="failed", error="视频生成未完成，请到供应商任务记录查看原因"
            )
        if status != "succeeded":
            raise ProviderError("服务返回未知任务状态，请继续查询原任务")
        return GenerationResult(
            status="succeeded", outputs=[self._output((data.get("content") or {}).get("video_url"))]
        )


@generation_registry.register("minimax_video")
class MiniMaxVideoProvider(NativeMedia):
    name = "minimax_video"
    allowed_parameters = {"duration", "resolution"}

    def validate_request(self, req):
        super().validate_request(req)
        if (
            req.provider_params.get("resolution") == "1080P"
            and req.provider_params.get("duration", 6) != 6
        ):
            raise CapabilityUnsupported("海螺 1080P 视频支持 6 秒，请调整清晰度或时长")
        if len(req.prompt) > 2000:
            raise CapabilityUnsupported("海螺提示词最多 2000 个字符，请缩短描述")

    def _checked(self, method, path, **kwargs):
        data = self._request(method, path, **kwargs)
        base = data.get("base_resp")
        if not isinstance(base, dict) or base.get("status_code") != 0:
            # Unknown application errors are not proof that no task was created.
            raise ProviderError("海螺服务返回错误，请检查密钥、余额、模型权限和上游任务记录")
        return data

    def submit(self, req):
        self.validate_request(req)
        body = {
            "model": self.config["model"],
            "prompt": req.prompt,
            "duration": 6,
            "resolution": "768P",
            **req.provider_params,
        }
        if req.references:
            body["first_frame_image"] = self._image(req.references[0])
        data = self._checked("POST", "/video_generation", json=body)
        task = data.get("task_id")
        if not isinstance(task, str) or not task:
            raise ProviderError("海螺服务未返回任务编号，请核对上游记录")
        return JobHandle(external_job_id=task)

    def poll(self, handle):
        data = self._checked(
            "GET", "/query/video_generation", params={"task_id": handle.external_job_id}
        )
        if data.get("status") in ("Preparing", "Queueing", "Processing"):
            return GenerationResult(status="running")
        if data.get("status") == "Fail":
            return GenerationResult(
                status="failed", error="海螺视频生成失败，请到供应商任务记录查看原因"
            )
        if data.get("status") != "Success" or not data.get("file_id"):
            raise ProviderError("海螺任务状态或结果暂不可用，请继续查询原任务")
        file = self._checked("GET", "/files/retrieve", params={"file_id": data["file_id"]})
        return GenerationResult(
            status="succeeded", outputs=[self._output((file.get("file") or {}).get("download_url"))]
        )
