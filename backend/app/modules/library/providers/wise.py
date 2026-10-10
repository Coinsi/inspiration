"""Authenticated client for our pinned upstream-WISE bridge."""

import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit

import httpx

from app.core.errors import CapabilityUnsupported, ProviderError
from app.modules.generation.media_engine import Canceled
from app.modules.library.providers.media_files import media_filename


class _CancelableFile:
    def __init__(self, stream, canceled):
        self.stream, self.canceled = stream, canceled

    def read(self, size=-1):
        if self.canceled():
            raise Canceled()
        return self.stream.read(size)

    def __getattr__(self, name):
        return getattr(self.stream, name)


class WiseClient:
    def __init__(self, base_url, token, *, client=None):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ProviderError("WISE 服务地址无效")
        if not token:
            raise ProviderError("WISE 服务访问凭证未配置")
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.client = client

    @contextmanager
    def connection(self):
        if self.client is not None:
            yield self.client
        else:
            with httpx.Client(
                timeout=httpx.Timeout(180, connect=5, write=180, pool=5),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                yield client

    def request(self, method, path, *, missing=False, timeout_seconds=180, **kwargs):
        try:
            with self.connection() as client:
                request = httpx.Request(
                    method,
                    self.base_url + path,
                    headers=self.headers,
                    extensions={"timeout": httpx.Timeout(timeout_seconds, connect=5).as_dict()},
                    **kwargs,
                )
                response = client.send(request, auth=None, follow_redirects=False)
                if missing and response.status_code == 404:
                    return None
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("Invalid response")
                return result
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 422:
                try:
                    detail = exc.response.json().get("detail")
                except (ValueError, AttributeError):
                    detail = None
                code = detail.get("code") if isinstance(detail, dict) else None
                messages = {
                    "reference_no_object": "参考图未检测到可用物体，请更换清晰的参考图",
                    "reference_region_no_match": (
                        "所选区域未覆盖检测到的物体，请重新框选或更换参考图"
                    ),
                }
                if isinstance(code, str) and code in messages:
                    raise CapabilityUnsupported(
                        messages[code], {"retryable": False, "reason": code}
                    ) from exc
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 503:
                try:
                    detail = exc.response.json().get("detail")
                except (ValueError, AttributeError):
                    detail = None
                if detail == "WISE is processing another task; retry search shortly":
                    raise ProviderError(
                        "素材索引正在处理其他任务，请稍后重试检索",
                        {"retryable": True, "reason": "busy"},
                    ) from exc
                # Translate only exact, fixed bridge diagnostics; never reflect
                # arbitrary upstream errors or internal model/file paths.
                if isinstance(detail, str) and detail in {
                    "Optional index fingerprint is outdated or unknown; "
                    "rebuild this project's index before searching",
                    "Optional model fingerprint changed during search; "
                    "rebuild the index before retrying",
                }:
                    raise ProviderError(
                        "检索模型已更新，请在素材的“处理与检索”中重新建立索引后再搜索",
                        {"retryable": False, "reason": "reindex_required"},
                    ) from exc
                if detail == (
                    "Optional model acceptance is unavailable; "
                    "complete the offline probe before searching"
                ):
                    raise ProviderError(
                        "检索模型尚未完成验证，请先在设置中检查素材处理服务状态",
                        {"retryable": False, "reason": "model_validation_required"},
                    ) from exc
            retryable = isinstance(exc, httpx.TransportError) or (
                isinstance(exc, httpx.HTTPStatusError)
                and (exc.response.status_code >= 500 or exc.response.status_code == 429)
            )
            raise ProviderError(
                "WISE 服务请求失败，请检查连接、模型和处理任务", {"retryable": retryable}
            ) from exc

    def health(self):
        return self.request("GET", "/health", timeout_seconds=4)

    def get_job(self, job_id):
        return self.request("GET", f"/v1/jobs/{uuid.UUID(str(job_id))}", missing=True)

    def submit(
        self,
        project_id,
        source_id,
        job_id,
        path,
        capabilities,
        *,
        canceled=lambda: False,
        restore_after_job_id=None,
        media_type="video",
        mime_type="video/mp4",
    ):
        import json

        from app.modules.library.optional_capabilities import require_optional

        require_optional(capabilities)
        route = f"/v1/projects/{uuid.UUID(str(project_id))}/sources/{uuid.UUID(str(source_id))}"
        data = {
            "job_id": str(job_id),
            "media_type": media_type,
            "capabilities": json.dumps(capabilities),
        }
        if restore_after_job_id is not None:
            data["restore_after_job_id"] = str(uuid.UUID(str(restore_after_job_id)))
        # httpx multipart reads the file incrementally. Never load a whole proxy into RAM.
        with path.open("rb") as stream:
            return self.request(
                "POST",
                route,
                data=data,
                files={
                    "file": (
                        media_filename(source_id, mime_type),
                        _CancelableFile(stream, canceled),
                        mime_type,
                    )
                },
            )

    def cancel(self, job_id):
        return self.request("POST", f"/v1/jobs/{uuid.UUID(str(job_id))}/cancel")

    def retry(self, job_id):
        return self.request("POST", f"/v1/jobs/{uuid.UUID(str(job_id))}/retry")

    def search(self, project_id, payload):
        return self.request(
            "POST", f"/v1/projects/{uuid.UUID(str(project_id))}/search", json=payload
        )

    def search_media(
        self,
        project_id,
        content,
        *,
        modality,
        source_ids,
        limit,
        mime_type="image/jpeg",
        bbox=None,
    ):
        import json

        data = {"modality": modality, "source_ids": json.dumps(source_ids), "limit": str(limit)}
        if bbox is not None:
            data["bbox"] = json.dumps(bbox, allow_nan=False)
        return self.request(
            "POST",
            f"/v1/projects/{uuid.UUID(str(project_id))}/search/media",
            data=data,
            files={"file": (media_filename("query", mime_type), content, mime_type)},
        )

    def delete(self, project_id, source_id, job_id, *, source_job_id=None):
        route = f"/v1/projects/{uuid.UUID(str(project_id))}/sources/{uuid.UUID(str(source_id))}"
        params = {"job_id": str(job_id)}
        if source_job_id is not None:
            params["source_job_id"] = str(uuid.UUID(str(source_job_id)))
        return self.request("DELETE", route, params=params)
