"""Explicit JSON video.v1 text-to-video protocol; never infer it from a model name.

Reference media use short-lived public URLs, minted only at submission time.
"""

from app.adapters.contracts import Capabilities, JobHandle, generation_registry, reference_error
from app.adapters.generation.openai_video import OpenAIVideoProvider
from app.core.errors import CapabilityUnsupported, ProviderError


@generation_registry.register("video_v1")
class VideoV1Provider(OpenAIVideoProvider):
    name = "video_v1"
    content_query = "?download=1"
    content_redirects = True

    def capabilities(self):
        from app.modules.media.generation_reference import public_base

        refs = min(int(self.config.get("max_reference_images", 0)), 14)
        videos = min(int(self.config.get("max_reference_videos", 0)), 10)
        reason = None
        if not refs and not videos:
            reason = "此模型尚未启用参考图或视频，请确认渠道能力并配置参考素材上限。"
        elif not public_base():
            reason = (
                "参考素材生成视频需要通过公网 HTTPS 网站提交，系统会自动生成素材链接。"
                "若已上线，请检查反向代理是否正确传递访问协议，以及正式签名密钥。"
            )
            refs = 0
            videos = 0
        return Capabilities(
            modalities={"video"},
            features=({"reference", "img2img"} if refs else set())
            | ({"video_reference"} if videos else set()),
            max_reference_images=refs,
            max_reference_videos=videos,
            reference_unavailable_reason=reason,
            max_video_seconds=self.config.get("max_video_seconds", 30),
            param_schema=self.config.get("param_schema", {}),
            strict_parameters=True,
        )

    def validate_request(self, req):
        if req.request_type != "video" or req.count != 1:
            raise CapabilityUnsupported("Video v1 每次生成一个视频，请将数量设为 1")
        if req.mask:
            raise CapabilityUnsupported("Video v1 不支持蒙版输入")
        if req.references:
            caps = self.capabilities()
            if problem := reference_error(req.references, caps):
                raise CapabilityUnsupported(problem)
            if any(r.role not in ("ref", "reference") for r in req.references):
                raise CapabilityUnsupported("此接入使用普通参考图，不支持首帧或尾帧角色")
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
        if req.references:
            from app.modules.media.generation_reference import reference_url

            if any(
                r.kind == "image"
                and (
                    not r.data_url
                    or not r.data_url.startswith(
                        ("data:image/png;", "data:image/jpeg;", "data:image/webp;")
                    )
                )
                for r in req.references
            ):
                raise CapabilityUnsupported("参考图必须为已读取的 PNG、JPEG 或 WebP 图片")
            if any(
                r.kind == "video" and r.mime not in {"video/mp4", "video/webm", "video/quicktime"}
                for r in req.references
            ):
                raise CapabilityUnsupported("参考视频必须为已校验的 MP4、WebM 或 MOV")
            fields["operation"] = (
                "video_to_video"
                if any(r.kind == "video" for r in req.references)
                else "image_to_video"
            )
            fields["media_inputs"] = [
                {"kind": r.kind, "role": "reference", "url": reference_url(r.blob_hash)}
                for r in req.references
            ]
        result = self._request("POST", "/videos", json=fields)
        if not isinstance(result.get("id"), str) or not result["id"]:
            raise ProviderError("视频服务未返回任务编号；请检查上游记录后再决定是否重试")
        return JobHandle(external_job_id=result["id"])
