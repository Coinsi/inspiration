"""JSON media protocols used by the built-in provider directory.

Only the initial submit creates a paid job. Polling reconstructs its route from the
persisted handle and never needs to replay a prompt or upload.
"""

import re
import time
import uuid
from urllib.parse import parse_qs, quote, urlsplit

import jwt

from app.adapters.contracts import GenerationResult, JobHandle
from app.adapters.generation.native_media import NativeMedia
from app.adapters.generation.openai_video import OpenAIVideoProvider
from app.core.errors import CapabilityUnsupported, ProviderError


def path_segment(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[\w.-]{1,256}", value, re.ASCII)
        or value in {".", ".."}
    ):
        raise ProviderError("模型或任务编号格式无效")
    return quote(value, safe="")


def first(values):
    return values[0] if isinstance(values, list) and values else None


class JsonMedia(NativeMedia):
    resumable = True
    max_refs = 0

    def submit(self, req):
        self.validate_request(req)
        path, body = self.submission(req)
        reply = self._request("POST", path, json=body)
        return self.accepted(reply, path)

    def body(self, req):
        return {"model": self.config["model"], "prompt": req.prompt, **req.provider_params}

    @staticmethod
    def handle(task):
        if not isinstance(task, str) or not task or len(task) > 1024:
            raise ProviderError("供应商未返回有效任务编号，请核对上游记录")
        return JobHandle(external_job_id=task)

    def progress(self, state, url=None):
        if not isinstance(state, str):
            raise ProviderError("任务状态暂不可用，请继续查询原任务")
        state = state.lower()
        if state in {
            "queued",
            "queueing",
            "submitted",
            "created",
            "pending",
            "running",
            "processing",
            "in_progress",
            "in_queue",
            "starting",
            "rendering",
        }:
            return GenerationResult(status="running")
        if state in {
            "failed",
            "fail",
            "error",
            "expired",
            "canceled",
            "cancelled",
            "timeout",
            "deleted",
            "request moderated",
            "content moderated",
        }:
            return GenerationResult(
                status="failed", error="供应商未能完成生成，请查看供应商任务记录"
            )
        if state in {"completed", "success", "succeeded", "succeed", "done", "ready"}:
            return GenerationResult(status="succeeded", outputs=[self._output(url)])
        raise ProviderError("供应商返回了未识别的任务状态，可继续查询原任务")


class SyncImage(JsonMedia):
    modality = "image"
    resumable = False

    def accepted(self, reply, path):
        output = self._output(self.result_url(reply))
        return JobHandle(external_job_id="sync-" + uuid.uuid4().hex, raw={"url": output.url})

    @staticmethod
    def result_url(reply):
        return (first(reply.get("data")) or {}).get("url")

    def poll(self, handle):
        return self.progress("completed", handle.raw.get("url"))


class MiniMaxImage(SyncImage):
    name = "minimax_image"
    max_refs = 1
    allowed_parameters = {"aspect_ratio"}

    def submission(self, req):
        body = {**self.body(req), "response_format": "url", "n": 1}
        if req.references:
            body["subject_reference"] = [
                {"type": "character", "image_file": self._image(req.references[0])}
            ]
        return "/image_generation", body

    def result_url(self, reply):
        if reply.get("base_resp", {}).get("status_code") != 0:
            raise ProviderError("MiniMax 未返回图片，请检查模型权限和供应商记录")
        return first(reply.get("data", {}).get("image_urls"))


class ZhipuImage(SyncImage):
    name = "zhipu_image"
    allowed_parameters = {"size"}

    def submission(self, req):
        return "/images/generations", self.body(req)


class ZhipuVideo(JsonMedia):
    name = "zhipu_video"
    max_refs = 2
    allowed_parameters = {"duration", "size"}

    def submission(self, req):
        body = self.body(req)
        if req.references:
            ordered = sorted(req.references, key=lambda ref: ref.role == "last_frame")
            images = [self._image(ref) for ref in ordered]
            body["image_url"] = images[0] if len(images) == 1 else images
        return "/videos/generations", body

    def accepted(self, reply, path):
        return self.handle(reply.get("id"))

    def poll(self, handle):
        data = self._request("GET", "/async-result/" + path_segment(handle.external_job_id))
        item = first(data.get("video_result")) or {}
        return self.progress(data.get("task_status"), item.get("url"))


class XaiImage(SyncImage):
    name = "xai_image"
    max_refs = 1
    allowed_parameters = {"aspect_ratio", "resolution"}

    def submission(self, req):
        body = self.body(req)
        if req.references:
            body["image"] = {"url": self._image(req.references[0]), "type": "image_url"}
            return "/images/edits", body
        return "/images/generations", body


class XaiVideo(JsonMedia):
    name = "xai_video"
    max_refs = 1
    allowed_parameters = {"aspect_ratio", "duration", "resolution"}

    def submission(self, req):
        body = self.body(req)
        if req.references:
            body["image"] = {"url": self._image(req.references[0])}
        return "/videos/generations", body

    def accepted(self, reply, path):
        return self.handle(reply.get("request_id"))

    def poll(self, handle):
        reply = self._request("GET", "/videos/" + path_segment(handle.external_job_id))
        return self.progress(reply.get("status"), (reply.get("video") or {}).get("url"))


class WaveSpeedImage(JsonMedia):
    name = "wavespeed_image"
    modality = "image"
    allowed_parameters = {"size"}

    def submission(self, req):
        segments = self.config["model"].split("/")
        if len(segments) < 2:
            raise CapabilityUnsupported("请从 WaveSpeed 模型页复制含服务商前缀的模型 ID")
        return "/" + "/".join(path_segment(s) for s in segments), {
            "prompt": req.prompt,
            **req.provider_params,
        }

    def accepted(self, reply, path):
        return self.handle(reply.get("data", {}).get("id"))

    def poll(self, handle):
        reply = self._request(
            "GET", "/predictions/" + path_segment(handle.external_job_id) + "/result"
        )
        data = reply.get("data", {})
        return self.progress(data.get("status"), first(data.get("outputs")))


class ViduVideo(JsonMedia):
    name = "vidu_video"
    max_refs = 1
    allowed_parameters = {"duration", "resolution", "aspect_ratio"}

    def _headers(self):
        return {"Authorization": "Token " + self.token}

    def submission(self, req):
        body = self.body(req)
        if req.references:
            body["images"] = [self._image(req.references[0])]
            return "/img2video", body
        return "/text2video", body

    def accepted(self, reply, path):
        return self.handle(reply.get("task_id"))

    def poll(self, handle):
        reply = self._request(
            "GET", "/tasks/" + path_segment(handle.external_job_id) + "/creations"
        )
        return self.progress(reply.get("state"), (first(reply.get("creations")) or {}).get("url"))


class HeyGenVideo(JsonMedia):
    name = "heygen_video"
    max_refs = 1
    allowed_parameters = {"duration", "aspect_ratio", "resolution"}

    def _headers(self):
        return {"X-Api-Key": self.token}

    def submission(self, req):
        body = {**self.body(req), "mode": "text_to_video"}
        if req.references:
            mime, encoded = self._image(req.references[0]).split(",", 1)
            body.update(
                mode="image_to_video",
                image={"type": "base64", "media_type": mime[5:-7], "data": encoded},
            )
        return "/models/videos", body

    def accepted(self, reply, path):
        return self.handle(reply.get("data", reply).get("video_id"))

    def poll(self, handle):
        reply = self._request("GET", "/models/videos/" + path_segment(handle.external_job_id))
        data = reply.get("data", reply)
        return self.progress(data.get("status"), data.get("video_url"))


class Kling(JsonMedia):
    max_refs = 1

    def _headers(self):
        access, separator, secret = self.token.partition(":")
        if not separator or not access or not secret:
            raise CapabilityUnsupported("请填写可灵控制台的 Access Key 和 Secret Key")
        now = int(time.time())
        token = jwt.encode(
            {"iss": access, "exp": now + 300, "nbf": now - 5}, secret, algorithm="HS256"
        )
        return {"Authorization": "Bearer " + token}

    def submission(self, req):
        path = (
            "/images/generations"
            if self.modality == "image"
            else ("/videos/image2video" if req.references else "/videos/text2video")
        )
        body = {"model_name": self.config["model"], "prompt": req.prompt, **req.provider_params}
        if "duration" in body:
            body["duration"] = str(body["duration"])
        if req.references:
            body["image"] = self._image(req.references[0]).split(",", 1)[1]
        return path, body

    @staticmethod
    def task(reply):
        if reply.get("code") != 0:
            raise ProviderError("可灵接口未返回任务，请检查账号权限及模型 ID")
        return reply.get("data", {})

    def accepted(self, reply, path):
        task_id = self.task(reply).get("task_id")
        return self.handle(path.rsplit("/", 1)[-1] + ":" + path_segment(task_id))

    def poll(self, handle):
        mode, _, task_id = handle.external_job_id.partition(":")
        routes = {
            "generations": "/images/generations/",
            "text2video": "/videos/text2video/",
            "image2video": "/videos/image2video/",
        }
        if mode not in routes:
            raise ProviderError("无法识别已保存的可灵任务类型")
        data = self.task(self._request("GET", routes[mode] + path_segment(task_id)))
        outputs = (data.get("task_result") or {}).get(
            "images" if self.modality == "image" else "videos"
        )
        return self.progress(data.get("task_status"), (first(outputs) or {}).get("url"))


class KlingImage(Kling):
    name = "kling_image"
    modality = "image"
    allowed_parameters = {"aspect_ratio"}


class KlingVideo(Kling):
    name = "kling_video"
    allowed_parameters = {"aspect_ratio", "duration"}


class BailianVideo(JsonMedia):
    name = "bailian_video"
    max_refs = 1
    allowed_parameters = {"duration", "size", "resolution"}

    def _headers(self):
        return {**super()._headers(), "X-DashScope-Async": "enable"}

    def submission(self, req):
        inputs = {"prompt": req.prompt}
        if req.references:
            inputs["img_url"] = self._image(req.references[0])
        return "/services/aigc/video-generation/video-synthesis", {
            "model": self.config["model"],
            "input": inputs,
            "parameters": req.provider_params,
        }

    def accepted(self, reply, path):
        return self.handle(reply.get("output", {}).get("task_id"))

    def poll(self, handle):
        data = self._request("GET", "/tasks/" + path_segment(handle.external_job_id)).get(
            "output", {}
        )
        return self.progress(data.get("task_status"), data.get("video_url"))


class BflVideo(JsonMedia):
    name = "bfl_video"
    max_refs = 2
    allowed_parameters = {"duration", "aspect_ratio"}

    def _headers(self):
        return {"x-key": self.token}

    def submission(self, req):
        body = {"prompt": req.prompt, "mode": "t2v", **req.provider_params}
        if req.references:
            refs = sorted(req.references, key=lambda ref: ref.role == "last_frame")
            images = [self._image(r).split(",", 1)[1] for r in refs]
            body.update(mode="i2v", keyframes=images[0] if len(images) == 1 else images)
        return "/v1/" + path_segment(self.config["model"]), body

    def polling_origin(self, url):
        target, base = urlsplit(url), urlsplit(self.endpoint)
        same_origin = (target.scheme, target.netloc) == (base.scheme, base.netloc)
        official = (
            base.scheme == target.scheme == "https"
            and base.hostname
            in {
                "api.bfl.ai",
                "api.eu1.bfl.ai",
                "api.us1.bfl.ai",
                "api.eu2.bfl.ai",
                "api.us2.bfl.ai",
            }
            and re.fullmatch(r"api\.(?:[a-z]{2}\d+\.)?bfl\.ai", target.netloc) is not None
        )
        if (
            not (same_origin or official)
            or target.username
            or target.password
            or target.fragment
            or target.path != base.path.rstrip("/") + "/v1/get_result"
            or set(parse_qs(target.query)) != {"id"}
        ):
            raise ProviderError("BFL 查询地址与配置的服务不匹配")
        return target

    def accepted(self, reply, path):
        url = reply.get("polling_url") or ""
        target = self.polling_origin(url)
        if parse_qs(target.query).get("id") != [reply.get("id")]:
            raise ProviderError("BFL 未返回对应的任务查询地址")
        return self.handle(url)

    def poll(self, handle):
        target = self.polling_origin(handle.external_job_id)
        data = self._request(
            "GET",
            target.path,
            origin=target.scheme + "://" + target.netloc,
            params=parse_qs(target.query),
        )
        return self.progress(data.get("status"), (data.get("result") or {}).get("sample"))


class OpenRouterVideo(JsonMedia):
    name = "openrouter_video"
    max_refs = 2
    allowed_parameters = {"aspect_ratio", "duration", "resolution"}

    def submission(self, req):
        body = self.body(req)
        if req.references:
            body["frame_images"] = [
                {
                    "type": "image_url",
                    "frame_type": "last_frame" if r.role == "last_frame" else "first_frame",
                    "image_url": {"url": self._image(r)},
                }
                for r in req.references
            ]
        return "/videos", body

    def accepted(self, reply, path):
        return self.handle(reply.get("id"))

    def poll(self, handle):
        path = "/videos/" + path_segment(handle.external_job_id)
        data = self._request("GET", path)
        if data.get("status") == "completed":
            downloader = OpenAIVideoProvider(self.endpoint, self.token, self.config)
            downloader.content_query = "?index=0"
            downloader.content_redirects = True
            return downloader._download(path)
        return self.progress(data.get("status"))


# Shared by the registry, capability editor, and job recovery, so adding a directory
# entry can never advertise a missing adapter or forget how to resume its jobs.
MEDIA_ADAPTERS = (
    MiniMaxImage,
    ZhipuImage,
    ZhipuVideo,
    XaiImage,
    XaiVideo,
    WaveSpeedImage,
    ViduVideo,
    HeyGenVideo,
    KlingImage,
    KlingVideo,
    BailianVideo,
    BflVideo,
    OpenRouterVideo,
)
