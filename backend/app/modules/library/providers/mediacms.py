"""HTTP contract for MediaCMS v9.1.0 (1ab99af4587bfc515582783c9ee19ae12e0fd065).

Verified against files/views/media.py, files/serializers.py and files/models/media.py.
The upstream upload serializer ignores ``state``. Deployment MUST set
PORTAL_WORKFLOW='private'; the adapter requires this explicit configuration and
verifies every media response. The instance must remain on a private network.

The additional transcription and idempotent-upload endpoints are implemented by
services/mediacms-bridge; they are not claimed to be native MediaCMS APIs.
API Token authentication does not authenticate nginx's plain Django media_auth
view: downloads from that protected endpoint need a service account session or a
separately authenticated internal bridge. Do not expose either to the browser.
"""

import hashlib
import math
import mimetypes
import os
import re
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

UPSTREAM_VERSION = "v9.1.0"
UPSTREAM_COMMIT = "1ab99af4587bfc515582783c9ee19ae12e0fd065"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,255}\Z")
_STATUSES = {"pending", "running", "fail", "success"}
_CHUNK_SIZE = 64 * 1024


def token_for_upload(upload_key: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", upload_key):
        raise MediaCMSError("Invalid MediaCMS upload key")
    return "ins_" + hashlib.sha256(upload_key.encode()).hexdigest()[:48]


class MediaCMSError(RuntimeError):
    """Safe error text: never includes tokens, upstream bodies or signed URLs."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class MediaCMSPrivacyError(MediaCMSError):
    def __init__(self, remote_token: str):
        super().__init__("MediaCMS did not create private media; verify PORTAL_WORKFLOW")
        # Caller must persist this identity for cleanup instead of retrying the upload.
        self.remote_token = remote_token


class MediaCMSCancelled(MediaCMSError):
    pass


class _CancelableUpload:
    def __init__(self, source, canceled):
        self.source, self.canceled = source, canceled

    def read(self, size=-1):
        if self.canceled():
            raise MediaCMSCancelled("MediaCMS upload canceled")
        return self.source.read(_CHUNK_SIZE if size < 0 else min(size, _CHUNK_SIZE))

    def __getattr__(self, name):
        return getattr(self.source, name)


@dataclass(frozen=True)
class MediaCMSEncoding:
    resolution: str
    codec: str
    status: str
    progress: float | None
    url: str | None
    encoding_id: int | None = None

    @property
    def is_original(self) -> bool:
        return self.resolution == "0-original"


@dataclass(frozen=True)
class MediaCMSSubtitle:
    url: str
    language: str
    label: str


@dataclass(frozen=True)
class MediaCMSMedia:
    remote_id: str
    state: str
    encoding_status: str
    media_type: str
    duration_seconds: float | None
    thumbnail_url: str | None
    poster_url: str | None
    preview_url: str | None
    sprites_url: str | None
    original_url: str | None
    encodings: tuple[MediaCMSEncoding, ...]
    subtitles: tuple[MediaCMSSubtitle, ...]
    hls: dict[str, str]

    @property
    def ready_encodings(self) -> tuple[MediaCMSEncoding, ...]:
        return tuple(
            item
            for item in self.encodings
            if not item.is_original and item.status == "success" and item.url
        )


def _number(value: Any, *, maximum: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or (maximum is not None and number > maximum):
        return None
    return number


def _origin(url: str) -> tuple[str, str, int]:
    if not isinstance(url, str) or any(ord(char) < 32 for char in url) or "\\" in url:
        raise MediaCMSError("Invalid MediaCMS URL")
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError
        if parts.username is not None or parts.password is not None or parts.fragment:
            raise ValueError
        return (
            parts.scheme,
            parts.hostname.lower(),
            parts.port or (443 if parts.scheme == "https" else 80),
        )
    except ValueError:
        raise MediaCMSError("Invalid MediaCMS URL") from None


class MediaCMSClient:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        private_workflow_confirmed: bool = False,
        allowed_media_origins: Iterable[str] = (),
        media_session_cookie: str | None = None,
        client: httpx.Client | None = None,
        timeout: httpx.Timeout | None = None,
    ):
        """All URLs, origin allowlists and credentials must come from server config.

        The injected client is borrowed and is not closed by this adapter. Its
        auth, cookies and redirect settings are deliberately not inherited.
        """
        self._origin = _origin(base_url)
        parts = urlsplit(base_url)
        if parts.query:
            raise MediaCMSError("MediaCMS base URL must not contain a query")
        if not api_token or any(char.isspace() for char in api_token):
            raise MediaCMSError("Invalid MediaCMS API token")
        if media_session_cookie and not re.fullmatch(r"[A-Za-z0-9]+", media_session_cookie):
            raise MediaCMSError("Invalid MediaCMS session cookie")
        self._base_url = base_url.rstrip("/") + "/"
        self._api_token = api_token
        self._session_cookie = media_session_cookie
        self._private = private_workflow_confirmed
        self._allowed_origins = {self._origin}
        for origin in allowed_media_origins:
            parsed = urlsplit(origin)
            if parsed.path not in {"", "/"} or parsed.query:
                raise MediaCMSError("Allowed media origins must not contain paths or queries")
            self._allowed_origins.add(_origin(origin))
        self._owns_client = client is None
        self._client = client or httpx.Client(trust_env=False)
        self._timeout = timeout or httpx.Timeout(connect=10, read=60, write=120, pool=10)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _api_url(self, remote_id: str | None = None) -> str:
        if remote_id is not None and not _TOKEN.fullmatch(remote_id):
            raise MediaCMSError("Invalid MediaCMS media token")
        return self._base_url + "api/v1/media" + (f"/{remote_id}" if remote_id else "")

    def _bridge_url(self, remote_id: str | None = None, suffix: str = "") -> str:
        if remote_id is not None and not _TOKEN.fullmatch(remote_id):
            raise MediaCMSError("Invalid MediaCMS media token")
        return (
            self._base_url
            + "api/inspiration/v1/media/"
            + (f"{remote_id}/{suffix}" if remote_id else "")
        )

    @staticmethod
    def upload_remote_id(upload_key: str) -> str:
        return token_for_upload(upload_key)

    def resolve_media_url(self, value: Any) -> str | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str) or "\\" in value or any(ord(c) < 32 for c in value):
            raise MediaCMSError("Invalid MediaCMS media URL")
        url = urljoin(self._base_url, value)
        if _origin(url) not in self._allowed_origins:
            raise MediaCMSError("MediaCMS returned a URL outside allowed media origins")
        return url

    def _request(self, method: str, url: str, *, stream: bool = False, **kwargs) -> httpx.Response:
        origin = _origin(url)
        if origin not in self._allowed_origins:
            raise MediaCMSError("MediaCMS request origin is not allowed")
        # httpx.Request (rather than Client.build_request) avoids inherited cookies,
        # auth headers, base URL and query parameters from a caller's shared client.
        headers = {"Accept": "application/json" if not stream else "*/*"}
        if origin == self._origin:
            headers["Authorization"] = f"Token {self._api_token}"
            if stream and self._session_cookie:
                headers["Cookie"] = f"sessionid={self._session_cookie}"
        request = httpx.Request(
            method,
            url,
            headers=headers,
            extensions={"timeout": self._timeout.as_dict()},
            **kwargs,
        )
        try:
            return self._client.send(request, auth=None, follow_redirects=False, stream=stream)
        except httpx.HTTPError:
            # A POST may have been accepted before a read timeout. Caller must
            # reconcile the remote identity; this method never retries writes.
            raise MediaCMSError(
                "MediaCMS request failed; check service connectivity",
                retryable=True,
            ) from None

    @staticmethod
    def _check(response: httpx.Response, accepted: set[int]) -> None:
        if response.status_code not in accepted:
            raise MediaCMSError(
                f"MediaCMS returned HTTP {response.status_code}",
                status_code=response.status_code,
                retryable=response.status_code == 429 or response.status_code >= 500,
            )

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            result = response.json()
        except ValueError:
            raise MediaCMSError("MediaCMS returned invalid JSON") from None
        if not isinstance(result, dict):
            raise MediaCMSError("MediaCMS returned an invalid media object")
        return result

    def _parse_media(self, data: dict, remote_id: str | None = None) -> MediaCMSMedia:
        # SingleMediaSerializer does not expose friendly_token: use requested ID.
        token = data.get("friendly_token") or remote_id
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise MediaCMSError("MediaCMS response is missing a valid media token")
        if remote_id and token != remote_id:
            raise MediaCMSError("MediaCMS returned a mismatched media token")
        if data.get("state") != "private":
            raise MediaCMSPrivacyError(token)
        status = data.get("encoding_status")
        if status not in _STATUSES:
            raise MediaCMSError("MediaCMS returned an unknown encoding status")
        encodings = []
        raw_encodings = data.get("encodings_info") or {}
        raw_subtitles = data.get("subtitles_info") or []
        raw_hls = data.get("hls_info") or {}
        if not isinstance(raw_encodings, dict) or not isinstance(raw_hls, dict):
            raise MediaCMSError("MediaCMS returned invalid encoding metadata")
        if not isinstance(raw_subtitles, list):
            raise MediaCMSError("MediaCMS returned invalid subtitle metadata")
        for resolution, codecs in raw_encodings.items():
            if not isinstance(codecs, dict):
                raise MediaCMSError("MediaCMS returned invalid encoding metadata")
            for codec, item in codecs.items():
                if not isinstance(item, dict) or item.get("status") not in _STATUSES:
                    raise MediaCMSError("MediaCMS returned invalid encoding metadata")
                encodings.append(
                    MediaCMSEncoding(
                        resolution=str(resolution),
                        codec=codec,
                        status=item["status"],
                        progress=_number(item.get("progress"), maximum=100),
                        url=self.resolve_media_url(item.get("url")),
                        encoding_id=item.get("encoding_id"),
                    )
                )
        subtitles = []
        for item in raw_subtitles:
            if not isinstance(item, dict):
                raise MediaCMSError("MediaCMS returned invalid subtitle metadata")
            url = self.resolve_media_url(item.get("src"))
            if not url:
                raise MediaCMSError("MediaCMS returned a subtitle without a URL")
            subtitles.append(
                MediaCMSSubtitle(url, str(item.get("srclang", "")), str(item.get("label", "")))
            )
        hls = {}
        for key, value in raw_hls.items():
            if url := self.resolve_media_url(value):
                hls[key] = url
        return MediaCMSMedia(
            remote_id=token,
            state="private",
            encoding_status=status,
            media_type=str(data.get("media_type", "")),
            duration_seconds=_number(data.get("duration")),
            thumbnail_url=self.resolve_media_url(data.get("thumbnail_url")),
            poster_url=self.resolve_media_url(data.get("poster_url")),
            preview_url=self.resolve_media_url(data.get("preview_url")),
            sprites_url=self.resolve_media_url(data.get("sprites_url")),
            original_url=self.resolve_media_url(data.get("original_media_url")),
            encodings=tuple(encodings),
            subtitles=tuple(subtitles),
            hls=hls,
        )

    def upload(
        self,
        path: Path,
        *,
        title: str,
        description: str = "",
        upload_key: str | None = None,
        source_sha256: str | None = None,
        mime_type: str | None = None,
        filename: str | None = None,
        canceled: Callable[[], bool] = lambda: False,
    ) -> MediaCMSMedia:
        if not self._private:
            raise MediaCMSError("Confirm private MediaCMS workflow before uploading")
        path = Path(path)
        if not path.is_file():
            raise MediaCMSError("MediaCMS upload source does not exist")
        if canceled():
            raise MediaCMSCancelled("MediaCMS upload canceled")
        content_type = mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = {"title": title, "description": description}
        url = self._api_url()
        if upload_key is not None:
            self.upload_remote_id(upload_key)
            if not source_sha256 or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
                raise MediaCMSError("A valid source SHA256 is required for idempotent upload")
            data.update(upload_key=upload_key, source_sha256=source_sha256)
            url = self._bridge_url()
        elif source_sha256 is not None:
            raise MediaCMSError("An upload key is required with source SHA256")
        # httpx's multipart encoder streams the file in bounded chunks.
        with path.open("rb") as source:
            response = self._request(
                "POST",
                url,
                data=data,
                files={
                    "media_file": (
                        filename or path.name,
                        _CancelableUpload(source, canceled),
                        content_type,
                    )
                },
            )
        self._check(response, {200, 201} if upload_key else {201})
        return self._parse_media(self._json(response))

    def get_media(self, remote_id: str, *, missing: bool = False) -> MediaCMSMedia | None:
        response = self._request("GET", self._api_url(remote_id))
        if missing and response.status_code == 404:
            return None
        if missing and response.status_code == 400:
            if self._json(response).get("detail") == "media file does not exist":
                return None
        self._check(response, {200})
        return self._parse_media(self._json(response), remote_id)

    def request_encode(self, remote_id: str, *, profile_id: int | None = None) -> None:
        if profile_id is None:
            # Reuse the upload's expected profiles and the post-commit dispatch
            # barrier; the native retry path can schedule untracked chunk jobs.
            response = self._request("POST", self._bridge_url(remote_id, "encode"), json={})
            self._check(response, {202})
            return
        # Upstream MediaDetail supports form data, and this action requires an editor.
        data = {"type": "encode"}
        if profile_id is not None:
            if isinstance(profile_id, bool) or not isinstance(profile_id, int) or profile_id < 1:
                raise MediaCMSError("Invalid MediaCMS encoding profile")
            data["encoding_profiles"] = str(profile_id)
        response = self._request("POST", self._api_url(remote_id), data=data)
        self._check(response, {201})

    def delete_media(self, remote_id: str) -> bool:
        response = self._request("DELETE", self._api_url(remote_id))
        if response.status_code == 404:
            return False
        # Upstream returns 400, not 404, for a missing friendly_token.
        if response.status_code == 400:
            if self._json(response).get("detail") == "media file does not exist":
                return False
        self._check(response, {204})
        return True

    def manifest(self, remote_id: str) -> dict:
        response = self._request("GET", self._bridge_url(remote_id, "manifest"))
        self._check(response, {200})
        data = self._json(response)
        if data.get("remote_id") != remote_id:
            raise MediaCMSError("MediaCMS returned a mismatched media token")
        if data.get("encoding_status") not in _STATUSES:
            raise MediaCMSError("MediaCMS returned an invalid processing manifest")
        for key in ("thumbnail", "poster", "preview", "sprites"):
            item = data.get(key)
            if item is not None:
                self._validate_manifest_item(item)
        for key in ("encodings", "subtitles"):
            if not isinstance(data.get(key), list):
                raise MediaCMSError("MediaCMS returned an invalid processing manifest")
            for item in data[key]:
                self._validate_manifest_item(item)
        progress = data.get("processing")
        if (
            not isinstance(progress, dict)
            or type(progress.get("complete")) is not bool
            or progress.get("dispatch")
            not in ("pending", "initializing", "submitting", "submitted", "failed")
            or progress.get("status") not in _STATUSES
            or any(
                type(progress.get(key)) is not int or progress[key] < 0
                for key in ("expected", "terminal", "pending", "running", "failed")
            )
            or progress["terminal"] > progress["expected"]
            or progress["failed"] > progress["terminal"]
            or (progress["complete"] and (progress["pending"] or progress["running"]))
            or progress["complete"] != (progress["status"] in ("success", "fail"))
            or (
                progress["complete"]
                and progress["dispatch"] == "submitted"
                and progress["terminal"] != progress["expected"]
            )
            or (
                progress["complete"]
                and progress["status"] == "fail"
                and progress["dispatch"] != "failed"
                and not progress["failed"]
            )
            or (
                progress["status"] == "success"
                and (
                    not progress["complete"]
                    or progress["dispatch"] != "submitted"
                    or progress["failed"]
                    or progress["terminal"] != progress["expected"]
                )
            )
        ):
            raise MediaCMSError("MediaCMS returned an invalid encoding completion checkpoint")
        return data

    def _validate_manifest_item(self, item: Any) -> None:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise MediaCMSError("MediaCMS returned an invalid output")
        url = self.resolve_media_url(item.get("url"))
        if not url:
            raise MediaCMSError("MediaCMS returned an output without a URL")
        item["url"] = url

    def get_transcription(self, remote_id: str) -> dict:
        response = self._request("GET", self._bridge_url(remote_id, "transcription"))
        self._check(response, {200})
        return self._transcription_json(response)

    def request_transcription(self, remote_id: str, *, retry: bool = False) -> dict:
        response = self._request(
            "POST",
            self._bridge_url(remote_id, "transcription"),
            json={"retry": retry},
        )
        self._check(response, {200, 202})
        return self._transcription_json(response)

    def _transcription_json(self, response: httpx.Response) -> dict:
        data = self._json(response)
        if data.get("status") not in _STATUSES | {"not_requested"}:
            raise MediaCMSError("MediaCMS returned an invalid transcription status")
        cues = data.get("cues", [])
        if not isinstance(cues, list) or len(cues) > 20000:
            raise MediaCMSError("MediaCMS returned invalid subtitle cues")
        for cue in cues:
            if not isinstance(cue, dict):
                raise MediaCMSError("MediaCMS returned invalid subtitle cues")
            start, end = cue.get("start_ms"), cue.get("end_ms")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or not isinstance(cue.get("text"), str)
            ):
                raise MediaCMSError("MediaCMS returned invalid subtitle cue timestamps")
        if data.get("subtitle") is not None:
            self._validate_manifest_item(data["subtitle"])
        return data

    def download(
        self,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
        canceled: Callable[[], bool] = lambda: False,
    ) -> int:
        """Copy one output, bounded and atomic. Does not crawl HLS child playlists."""
        if max_bytes < 1:
            raise MediaCMSError("A positive output size limit is required")
        resolved = self.resolve_media_url(url)
        if not resolved:
            raise MediaCMSError("A media URL is required")
        if canceled():
            raise MediaCMSCancelled("MediaCMS download canceled")
        destination = Path(destination)
        response = self._request("GET", resolved, stream=True)
        temp_path = None
        try:
            self._check(response, {200})
            declared = response.headers.get("content-length")
            if declared and (not declared.isdigit() or int(declared) > max_bytes):
                raise MediaCMSError("MediaCMS output exceeds its size limit")
            received = 0
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".mediacms-",
                dir=destination.parent,
                delete=False,
            ) as output:
                temp_path = Path(output.name)
                for chunk in response.iter_bytes(_CHUNK_SIZE):
                    if canceled():
                        raise MediaCMSCancelled("MediaCMS download canceled")
                    received += len(chunk)
                    if received > max_bytes:
                        raise MediaCMSError("MediaCMS output exceeds its size limit")
                    output.write(chunk)
            if declared and not response.headers.get("content-encoding"):
                if received != int(declared):
                    raise MediaCMSError("MediaCMS output ended before its declared size")
            if canceled():
                raise MediaCMSCancelled("MediaCMS download canceled")
            os.replace(temp_path, destination)
            return received
        except httpx.HTTPError:
            raise MediaCMSError("MediaCMS output download failed", retryable=True) from None
        finally:
            response.close()
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
