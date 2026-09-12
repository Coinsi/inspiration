"""GPT Image 生成供应商(OpenAI 兼容 Images API,仅图片、无视频)。

- 文生图:POST {endpoint}/images/generations(JSON),返回 b64_json。
- 带参考图:POST {endpoint}/images/edits(multipart,参考图来自服务层物化的 base64 data URI)。
- 同步 API:submit 即完成,结果暂存内存句柄,poll 直接返回 succeeded。
- endpoint 默认 https://api.openai.com/v1(可指向任意 OpenAI 兼容代理);model 走 config(默认 gpt-image-1)。
"""

import base64
import json
import urllib.request
import uuid

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

_DEFAULT_ENDPOINT = "https://api.openai.com/v1"


def _parse_data_url(data_url: str) -> tuple[str, bytes]:
    """data:image/png;base64,xxx → (mime, bytes)"""
    head, b64 = data_url.split(",", 1)
    mime = head.split(":", 1)[1].split(";", 1)[0] if ":" in head else "image/png"
    return mime, base64.b64decode(b64)


@generation_registry.register("gpt_image")
class GptImageProvider(GenerationProvider):
    name = "gpt_image"

    def __init__(
        self, endpoint: str | None = None, token: str | None = None, config: dict | None = None, **_
    ):
        self.endpoint = (endpoint or _DEFAULT_ENDPOINT).rstrip("/")
        self.token = token
        self.config = config or {}

    def _require(self):
        if not self.token:
            raise ProviderError("GPT Image 未配置 API Key(请在项目设置中配置)")

    def capabilities(self) -> Capabilities:
        return Capabilities(
            modalities={"image"},  # 仅图片,无视频
            features={"reference", "img2img"},
            max_reference_images=int(self.config.get("max_reference_images", 4)),
            param_schema=self.config.get("param_schema", {}),
        )

    def estimate_cost(self, req: GenerationRequest) -> CostEstimate:
        rate = self.config.get("rate", {"image": 30})
        unit = rate.get("image", 30)
        return CostEstimate(points=unit * req.count, detail={"unit": unit, "count": req.count})

    def _model(self) -> str:
        return self.config.get("model") or "gpt-image-1"

    def _size(self, req: GenerationRequest) -> str:
        return str(req.provider_params.get("size") or self.config.get("size") or "1024x1024")

    def submit(self, req: GenerationRequest) -> JobHandle:
        self._require()
        if req.request_type != "image":
            raise ProviderError("GPT Image 仅支持图片生成")
        refs = [r for r in req.references if r.data_url]
        try:
            data = self._call_with_n_fallback(req, refs)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"GPT Image 调用失败:{exc}") from exc
        # 同步完成:b64 结果暂存句柄,poll 时解码
        b64s = [item.get("b64_json") for item in data.get("data", []) if item.get("b64_json")]
        urls = [item.get("url") for item in data.get("data", []) if item.get("url")]
        if not b64s and not urls:
            raise ProviderError(f"GPT Image 未返回图片:{str(data)[:300]}")
        if len(b64s) + len(urls) != req.count:
            raise ProviderError(
                f"请求 {req.count} 张图片，供应商仅返回 {len(b64s) + len(urls)} 张，未按完整结果保存"
            )
        return JobHandle(
            external_job_id=f"gpt-image-{uuid.uuid4().hex[:8]}",
            raw={"b64s": b64s, "urls": urls, "usage": data.get("usage")},
        )

    def _call_with_n_fallback(self, req: GenerationRequest, refs) -> dict:
        """文生图默认走**流式单张循环**(SSE 持续有数据流动,规避代理网关 504 空闲超时,
        且天然不发 n 参数);流式被服务拒绝时自动退回非流式。
        图生图(edits)保持非流式:count==1 不发 n,count>1 先带 n、被拒则循环单张。
        """
        if not refs:
            merged: list = []
            usage = []
            for _ in range(max(1, req.count)):
                result = self._generate_one(req)
                merged.extend(result.get("data", []))
                if result.get("usage"):
                    usage.append(result["usage"])
            return {"data": merged, "usage": usage}

        call = lambda n: self._edits(req, refs, n)  # noqa: E731
        if req.count <= 1:
            return call(None)
        try:
            return call(req.count)
        except ProviderError as e:
            if not self._unsupported(e, {"n"}):
                raise
            merged = []
            for _ in range(req.count):
                merged.extend(call(None).get("data", []))
            return {"data": merged}

    def _generate_one(self, req: GenerationRequest) -> dict:
        """单张文生图:优先流式;服务不支持 stream 参数时退回非流式。"""
        if self.config.get("stream", not self._model().startswith("gpt-image-2")):
            try:
                return self._generations_stream(req)
            except ProviderError as e:
                # 服务不认识 stream/partial_images 参数 → 退回非流式
                if self._unsupported(e, {"stream", "partial_images"}):
                    return self._generations(req, None)
                raise
        return self._generations(req, None)

    @staticmethod
    def _unsupported(error: ProviderError, names: set[str]) -> bool:
        return (
            error.detail.get("code") in {"unknown_parameter", "unsupported_parameter"}
            and error.detail.get("param") in names
        )

    @staticmethod
    def _http_error(error):
        detail = {}
        try:
            payload = json.loads(error.read())
            if isinstance(payload.get("error"), dict):
                detail = {k: payload["error"].get(k) for k in ("code", "param")}
                message = str(payload["error"].get("message", ""))[:500]
            else:
                message = str(error.reason)
        except Exception:
            message = str(error.reason)
        return ProviderError(f"GPT Image HTTP {error.code}: {message}", detail)

    def _generations_stream(self, req: GenerationRequest) -> dict:
        """只接收 completed 事件；partial 不能替代成功结果。"""
        body = {
            "model": self._model(),
            "prompt": req.prompt,
            "size": self._size(req),
            "stream": True,
            **{k: v for k, v in req.provider_params.items() if k not in ("size", "n", "stream")},
        }
        quality = req.provider_params.get("quality") or self.config.get("quality")
        if quality:
            body["quality"] = quality
        request = urllib.request.Request(
            f"{self.endpoint}/images/generations",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {self.token}",
            },
        )
        completed_b64: str | None = None
        usage = None
        try:
            with urllib.request.urlopen(request, timeout=600) as resp:  # noqa: S310
                ctype = resp.headers.get("Content-Type", "")
                if "text/event-stream" not in ctype:
                    # 服务忽略了 stream,按普通 JSON 返回
                    return json.loads(resp.read())
                for raw in resp:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        evt = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    b64 = evt.get("b64_json")
                    if not b64 and isinstance(evt.get("data"), list) and evt["data"]:
                        b64 = evt["data"][0].get("b64_json")
                    etype = str(evt.get("type", ""))
                    if etype == "error" or evt.get("error"):
                        raise ProviderError("GPT Image 流式请求失败，未保存预览图")
                    if b64 and etype in {"image_generation.completed", "image_edit.completed"}:
                        completed_b64 = b64
                        usage = evt.get("usage")
        except urllib.error.HTTPError as e:
            raise self._http_error(e) from e
        if not completed_b64:
            raise ProviderError("GPT Image 流已结束但未收到完成事件，未保存预览图")
        return {"data": [{"b64_json": completed_b64}], "usage": usage}

    def _http(self, request: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=300) as resp:  # noqa: S310
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise self._http_error(e) from e

    def _generations(self, req: GenerationRequest, n: int | None) -> dict:
        body = {
            "model": self._model(),
            "prompt": req.prompt,
            "size": self._size(req),
            **{k: v for k, v in req.provider_params.items() if k not in ("size", "n")},
        }
        quality = req.provider_params.get("quality") or self.config.get("quality")
        if quality:
            body["quality"] = quality  # low/medium/high:低质量出图快,可规避代理网关超时
        if n is not None:
            body["n"] = n
        request = urllib.request.Request(
            f"{self.endpoint}/images/generations",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        return self._http(request)

    def _edits(self, req: GenerationRequest, refs, n: int | None) -> dict:
        """带参考图:multipart 调 /images/edits(image[] 多文件)。"""
        boundary = "----inspiration" + uuid.uuid4().hex
        parts: list[bytes] = []

        def field(name: str, value: str):
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            )

        field("model", self._model())
        field("prompt", req.prompt)
        field("size", self._size(req))
        quality = req.provider_params.get("quality") or self.config.get("quality")
        if quality:
            field("quality", str(quality))
        if n is not None:
            field("n", str(n))
        for i, r in enumerate(refs):
            mime, blob = _parse_data_url(r.data_url)
            ext = mime.split("/")[-1] or "png"
            parts.append(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; '
                    f'filename="ref{i}.{ext}"\r\nContent-Type: {mime}\r\n\r\n'
                ).encode()
                + blob
                + b"\r\n"
            )
        parts.append(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            f"{self.endpoint}/images/edits",
            data=b"".join(parts),
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": f"Bearer {self.token}",
            },
        )
        return self._http(request)

    def poll(self, handle: JobHandle) -> GenerationResult:
        outputs: list[Output] = []
        for b64 in handle.raw.get("b64s", []):
            try:
                outputs.append(Output(data=base64.b64decode(b64, validate=True), type="image"))
            except Exception:  # noqa: BLE001
                return GenerationResult(status="failed", error="供应商返回了损坏的图片编码")
        for url in handle.raw.get("urls", []):
            outputs.append(Output(url=url, type="image"))
        if not outputs:
            return GenerationResult(status="failed", error="未取得图片数据")
        return GenerationResult(
            status="succeeded",
            outputs=outputs,
            cost_raw={"provider": "gpt_image", "usage": handle.raw.get("usage")},
        )
