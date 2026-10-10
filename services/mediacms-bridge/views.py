"""Service-account-only endpoints built on real MediaCMS models and Celery tasks.

Pinned upstream: v9.1.0 / 1ab99af4587bfc515582783c9ee19ae12e0fd065.
No user supplied filesystem paths, remote URLs or command strings are accepted.
"""

import hashlib
import hmac
import json
import re
import time
import uuid

from django.conf import settings
from django.db import connection, transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from files.models import EncodeProfile, Media
from files.models.subtitle import TranscriptionRequest
from files.models.utils import PREVIEW_PROFILE_NAME
from files.serializers import MediaSerializer
from kombu.exceptions import OperationalError
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import (
    APIException,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from .capacity import DiskCapacityError, require_space
from .outputs import output_item
from .uploadhandler import CapacityUploadHandler, StorageUnavailable

PIPELINE_MARKER = "\ninspiration-processing:"


class Conflict(APIException):
    status_code = 409
    default_detail = "Upload key already belongs to another source"


class Unavailable(APIException):
    status_code = 503
    default_detail = "Media processing service is unavailable"


class ServiceAccountOnly(BasePermission):
    def has_permission(self, request, view):
        expected = getattr(settings, "INSPIRATION_SERVICE_TOKEN", "")
        actual = getattr(request.auth, "key", "")
        return bool(
            expected
            and actual
            and request.user.is_active
            and hmac.compare_digest(expected, actual)
        )


class ServiceView(APIView):
    authentication_classes = (TokenAuthentication,)
    permission_classes = (ServiceAccountOnly,)
    parser_classes = (JSONParser,)

    @staticmethod
    def media(request, token, *, lock=False):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", token):
            raise NotFound()
        queryset = Media.objects
        if lock:
            queryset = queryset.select_for_update()
        # Even a service account that is an upstream superuser cannot inspect
        # another uploader's files through this extension.
        media = get_object_or_404(
            queryset, friendly_token=token, user_id=request.user.pk
        )
        if media.state != "private":
            raise PermissionDenied("Only private integration media are accessible")
        return media


def _file_path(field):
    return field.path if field else None


def _pipeline_snapshot(media):
    _, separator, payload = media.description.partition(PIPELINE_MARKER)
    if not separator:
        return None
    try:
        data = json.loads(payload)
        if (
            not isinstance(data, dict)
            or data.get("dispatch")
            not in ("initializing", "submitting", "submitted", "failed")
            or not isinstance(data.get("profiles"), list)
            or any(type(value) is not int or value < 1 for value in data["profiles"])
        ):
            raise ValueError()
        return data
    except (ValueError, TypeError):
        raise Unavailable("Processing checkpoint is invalid") from None


def _encoding_progress(media):
    from .recovery import available_lock

    snapshot = _pipeline_snapshot(media)
    rows = list(media.encodings.all())
    pending = sum(row.status == "pending" for row in rows)
    running = sum(row.status == "running" for row in rows)
    # Database terminal state can precede the native task's final post-processing.
    # A live runner lease always prevents cleanup, even after a publish timeout
    # incorrectly caused an upstream row to be marked failed.
    active = 0
    for kind, identifier in [
        ("initialize", media.pk),
        *(("encode", row.pk) for row in rows),
    ]:
        with available_lock(kind, identifier) as free:
            active += not free
    running = max(running, active)
    latest = {}
    for row in rows:
        if not row.chunk and (
            row.profile_id not in latest or row.pk > latest[row.profile_id].pk
        ):
            latest[row.profile_id] = row
    expected = set(snapshot["profiles"]) if snapshot else set()
    terminal = sum(
        profile in latest and latest[profile].status in ("success", "fail")
        for profile in expected
    )
    failed = sum(
        profile in latest and latest[profile].status == "fail" for profile in expected
    )
    dispatch = snapshot["dispatch"] if snapshot else "pending"
    # Whole-profile dispatch has returned and no task can create more Encoding
    # rows. A missing expected row is an explicit failure, not endless progress.
    if (
        dispatch == "submitted"
        and not pending
        and not running
        and terminal < len(expected)
    ):
        dispatch = "failed"
    complete = (
        dispatch == "submitted"
        and (bool(expected) or media.media_type != "video")
        and terminal == len(expected)
        and pending == 0
        and running == 0
    )
    dispatch_failed = dispatch == "failed" and pending == 0 and running == 0
    status = (
        "fail"
        if dispatch_failed or (complete and failed)
        else "success"
        if complete
        else "running"
    )
    return {
        "dispatch": dispatch,
        "complete": complete or dispatch_failed,
        "status": status,
        "expected": len(expected),
        "terminal": terminal,
        "pending": pending,
        "running": running,
        "failed": failed,
        "error": snapshot.get("error") if snapshot else None,
    }


def _record_pipeline(media, profiles, dispatch):
    marker = media.description.partition(PIPELINE_MARKER)[0]
    media.description = (
        marker
        + PIPELINE_MARKER
        + json.dumps(
            {
                "profiles": [profile.pk for profile in profiles],
                "dispatch": dispatch,
                "updated": time.time(),
            },
            separators=(",", ":"),
        )
    )
    # Avoid triggering upstream post_save and do not overwrite concurrent media fields.
    Media.objects.filter(pk=media.pk).update(description=media.description)


def _mark_pipeline_failed(media, reason=None):
    if getattr(media, "media_type", None) == "audio":
        from .audio_preview import AUDIO_PROFILE

        # The supervised initialization process is already dead when this runs.
        media.encodings.filter(
            profile__name=AUDIO_PROFILE, status__in=("pending", "running")
        ).update(status="fail")
    snapshot = _pipeline_snapshot(media) or {"profiles": []}
    snapshot.update(dispatch="failed", updated=time.time())
    if reason:
        snapshot["error"] = str(reason)[:500]
    media.description = (
        media.description.partition(PIPELINE_MARKER)[0]
        + PIPELINE_MARKER
        + json.dumps(snapshot, separators=(",", ":"))
    )
    Media.objects.filter(pk=media.pk).update(description=media.description)


def _eligible_profiles(media):
    return [
        profile
        for profile in EncodeProfile.objects.filter(active=True)
        if profile.name == PREVIEW_PROFILE_NAME
        or not media.video_height
        or not profile.resolution
        or profile.resolution <= media.video_height
        or profile.resolution in settings.MINIMUM_RESOLUTIONS_TO_ENCODE
    ]


def _execute_encodings(media, profiles, encode=None):
    from .recovery import available_lock
    from .tasks import enqueue_encodings

    with available_lock("dispatch", media.pk) as free:
        if not free:
            return
        _execute_encoding_batch(
            media, profiles, encode or (lambda **_: enqueue_encodings(media, profiles))
        )


def _execute_encoding_batch(media, profiles, encode):
    try:
        # Whole-file FFmpeg is streamed and bounded by the worker concurrency.
        # No upstream chunkize task may create new profiles after our completion
        # check. Long videos resume at a whole-profile retry, not a chunk boundary.
        if not profiles:
            raise Unavailable("No eligible encoding profiles are enabled")
        encode(profiles=list(profiles), chunkize=False)
    except Exception:
        _record_pipeline(media, profiles, "failed")
        raise
    _record_pipeline(media, profiles, "submitted")


def _dispatch_encodings(media, encode=None):
    profiles = _eligible_profiles(media)
    _record_pipeline(media, profiles, "submitting")
    _execute_encodings(media, profiles, encode)


def _outputs(media):
    selected = {}

    def add(key, value, **extra):
        result = output_item(
            media.friendly_token, key, value, settings.MEDIA_ROOT, **extra
        )
        if result is not None:
            selected[key] = result

    add("thumbnail", _file_path(media.uploaded_thumbnail or media.thumbnail))
    add("poster", _file_path(media.uploaded_poster or media.poster))
    add("sprites", _file_path(media.sprites))
    preview = media.encodings.filter(
        profile__name=PREVIEW_PROFILE_NAME,
        status="success",
        chunk=False,
    ).first()
    if preview:
        add("preview", _file_path(preview.media_file))
    elif media.preview_file_path:
        add("preview", media.preview_file_path)
    for encoding in media.encodings.select_related("profile").filter(
        status="success",
        chunk=False,
    ):
        if encoding.profile.name == PREVIEW_PROFILE_NAME:
            continue
        add(
            f"encoding-{encoding.pk}",
            _file_path(encoding.media_file),
            id=encoding.pk,
            resolution=encoding.profile.resolution,
            codec=encoding.profile.codec,
        )
    for subtitle in media.subtitles.select_related("language").all():
        add(
            f"subtitle-{subtitle.pk}",
            _file_path(subtitle.subtitle_file),
            id=subtitle.pk,
            language=subtitle.language.code,
            label=subtitle.language.title,
        )
    return selected


def _transcription_state(media):
    from .recovery import available_lock

    row = (
        media.transcriptionrequests.filter(translate_to_english=False)
        .order_by("-pk")
        .first()
    )
    status = row.status if row else "not_requested"
    if row:
        with available_lock("speech", row.pk) as free:
            if not free:
                status = "running"
    return {
        "request_id": row.pk if row else None,
        "status": status,
        "translate_to_english": False,
    }


def _transcription_with_cues(media):
    import pysubs2

    data = _transcription_state(media)
    data["cues"] = []
    if data["status"] != "success":
        return data
    subtitle = (
        media.subtitles.select_related("language")
        .filter(
            language__code="whisper",
        )
        .order_by("-pk")
        .first()
    )
    if subtitle is None:
        raise Unavailable("Transcription output is not available yet")
    result = output_item(
        media.friendly_token,
        f"subtitle-{subtitle.pk}",
        _file_path(subtitle.subtitle_file),
        settings.MEDIA_ROOT,
    )
    if result is None:
        raise Unavailable("Transcription output is not available yet")
    info, path = result
    if info["size_bytes"] > 8 * 1024 * 1024:
        raise Unavailable("Transcription exceeds the configured subtitle import limit")
    try:
        cues = pysubs2.load(str(path), encoding="utf-8", format_="vtt")
    except (OSError, UnicodeError, ValueError, pysubs2.exceptions.Pysubs2Error):
        raise Unavailable("Transcription output could not be parsed") from None
    if len(cues) > 20000:
        raise Unavailable("Transcription exceeds the configured subtitle cue limit")
    for cue in cues:
        if cue.is_comment or not cue.plaintext.strip():
            continue
        if cue.start < 0 or cue.end <= cue.start:
            raise Unavailable("Transcription contains invalid cue timestamps")
        data["cues"].append(
            {
                "start_ms": cue.start,
                "end_ms": cue.end,
                "text": cue.plaintext.strip(),
                "language": "und",
            }
        )
    data["subtitle"] = info
    return data


class ManifestView(ServiceView):
    def get(self, request, token):
        media = self.media(request, token)
        from .recovery import recover_media

        recover_media(media)
        media.refresh_from_db()
        items = {key: value[0] for key, value in _outputs(media).items()}
        return Response(
            {
                "remote_id": token,
                "encoding_status": media.encoding_status,
                "processing": _encoding_progress(media),
                "thumbnail": items.get("thumbnail"),
                "poster": items.get("poster"),
                "preview": items.get("preview"),
                "sprites": items.get("sprites"),
                "encodings": [
                    value for key, value in items.items() if key.startswith("encoding-")
                ],
                "subtitles": [
                    value for key, value in items.items() if key.startswith("subtitle-")
                ],
                "transcription": _transcription_state(media),
            }
        )


class OutputView(ServiceView):
    def get(self, request, token, output_key):
        media = self.media(request, token)
        selected = _outputs(media).get(output_key)
        if selected is None:
            raise NotFound("Output is not ready or does not exist")
        info, path = selected
        try:
            response = FileResponse(
                path.open("rb"),
                as_attachment=True,
                filename=path.name,
                content_type=info["mime"],
            )
        except FileNotFoundError:
            raise NotFound("Output no longer exists") from None
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


def initialize_media(media):
    media.encode = lambda: _dispatch_encodings(media)
    # The library imports full-length MP4 and poster, not sprite sheets. Avoid
    # dispatching another whole-video reader that outlives the selected outputs.
    media.produce_sprite_from_video = lambda: True
    media.media_init()
    if media.media_type == "audio":
        from .audio_preview import build_audio_preview

        build_audio_preview(media)
    if media.media_type != "video":
        _record_pipeline(media, [], "submitted")


def _save_new_media(media):
    from .tasks import enqueue_initialization

    # Persist the initialization outbox before saving. Upstream post_save only
    # schedules our guarded runner after commit, never starts a long web child.
    media.description += PIPELINE_MARKER + json.dumps(
        {
            "profiles": [],
            "dispatch": "initializing",
            "updated": time.time(),
            "task_id": "inspiration-" + uuid.uuid4().hex,
        },
        separators=(",", ":"),
    )
    media.media_init = lambda: transaction.on_commit(
        lambda: enqueue_initialization(media)
    )
    media._skip_admin_notification = True
    media.save()


class UploadView(ServiceView):
    parser_classes = (MultiPartParser, FormParser)

    def initialize_request(self, request, *args, **kwargs):
        # Install before DRF parses request.data. The default size check in post
        # runs only after the entire multipart body has already reached disk.
        request.upload_handlers = [CapacityUploadHandler(request)]
        return super().initialize_request(request, *args, **kwargs)

    def post(self, request):
        if getattr(settings, "PORTAL_WORKFLOW", None) != "private":
            raise Unavailable("MediaCMS private workflow is not configured")
        upload_key = request.data.get("upload_key", "")
        expected_hash = request.data.get("source_sha256", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", upload_key):
            raise ValidationError("Invalid upload key")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValidationError("Invalid source SHA256")
        upload = request.FILES.get("media_file")
        if upload is None:
            raise ValidationError("media_file is required")
        limit = getattr(settings, "INSPIRATION_MAX_UPLOAD_BYTES", 100 * 1024**3)
        if upload.size > limit:
            raise ValidationError("Source exceeds configured upload size limit")
        digest = hashlib.sha256()
        for chunk in upload.chunks(chunk_size=1024 * 1024):
            digest.update(chunk)
        if not hmac.compare_digest(digest.hexdigest(), expected_hash):
            raise ValidationError("Source SHA256 does not match uploaded bytes")
        upload.seek(0)
        key_digest = hashlib.sha256(upload_key.encode()).hexdigest()
        token = "ins_" + key_digest[:48]
        marker = f"inspiration:{expected_hash}:{upload.size}"
        # A fixed friendly_token plus a PostgreSQL transaction lock prevents two
        # concurrently retried POSTs from creating duplicate files/tasks.
        with transaction.atomic():
            lock_id = int(key_digest[:16], 16)
            if lock_id >= 2**63:
                lock_id -= 2**64
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])
            media = Media.objects.filter(friendly_token=token).first()
            created = media is None
            if media is not None:
                if (
                    media.user_id != request.user.pk
                    or media.description.partition(PIPELINE_MARKER)[0] != marker
                ):
                    raise Conflict()
                if media.state != "private":
                    raise PermissionDenied("Integration media is no longer private")
            else:
                try:
                    # Upload staging and final media can be different mounts;
                    # saving may require a second full copy of this source.
                    require_space(settings, (settings.MEDIA_ROOT,), additional_bytes=upload.size)
                except DiskCapacityError as exc:
                    raise StorageUnavailable(str(exc)) from exc
                media = Media(
                    friendly_token=token,
                    user=request.user,
                    media_file=upload,
                    title=str(request.data.get("title", ""))[:100],
                    description=marker,
                )
                _save_new_media(media)
            data = MediaSerializer(media, context={"request": request}).data
        return Response(data, status=201 if created else 200)


class EncodeView(ServiceView):
    def post(self, request, token):
        with transaction.atomic():
            media = self.media(request, token, lock=True)
            progress = _encoding_progress(media)
            if (
                progress["pending"]
                or progress["running"]
                or progress["dispatch"] == "submitting"
            ):
                return Response(progress, status=202)
            if media.media_type == "audio":
                from .tasks import enqueue_initialization

                media.description = (
                    media.description.partition(PIPELINE_MARKER)[0]
                    + PIPELINE_MARKER
                    + json.dumps(
                        {
                            "profiles": [],
                            "dispatch": "initializing",
                            "updated": time.time(),
                            "task_id": "inspiration-" + uuid.uuid4().hex,
                        }
                    )
                )
                Media.objects.filter(pk=media.pk).update(description=media.description)
                transaction.on_commit(lambda: enqueue_initialization(media))
                return Response(_encoding_progress(media), status=202)
            snapshot = _pipeline_snapshot(media)
            profiles = (
                list(EncodeProfile.objects.filter(pk__in=snapshot["profiles"]))
                if snapshot
                else _eligible_profiles(media)
            )
            if snapshot and len(profiles) != len(set(snapshot["profiles"])):
                raise Unavailable("An expected encoding profile no longer exists")
            _record_pipeline(media, profiles, "submitting")
            transaction.on_commit(lambda: _execute_encodings(media, profiles))
        return Response(_encoding_progress(media), status=202)


class TranscriptionView(ServiceView):
    def get(self, request, token):
        from .recovery import recover_media

        media = self.media(request, token)
        recover_media(media)
        return Response(_transcription_with_cues(media))

    def post(self, request, token):
        from .recovery import new_speech_delivery
        from .tasks import enqueue_transcription

        if not getattr(settings, "USE_WHISPER_TRANSCRIBE", False):
            raise Unavailable("Whisper transcription is not enabled on MediaCMS")
        retry = request.data.get("retry", False)
        if not isinstance(retry, bool):
            raise ValidationError("retry must be a boolean")
        with transaction.atomic():
            media = self.media(request, token, lock=True)
            if media.media_type not in {"audio", "video"}:
                raise ValidationError("Transcription requires audio or video media")
            row = (
                media.transcriptionrequests.filter(
                    translate_to_english=False,
                )
                .order_by("-pk")
                .first()
            )
            if row and (row.status != "fail" or not retry):
                return Response(_transcription_state(media))
            if row is None:
                row = TranscriptionRequest.objects.create(
                    media=media,
                    translate_to_english=False,
                    logs=new_speech_delivery(),
                )
            else:
                row.status, row.logs = "pending", new_speech_delivery()
                row.save(update_fields=["status", "logs"])
        # The pending row must be visible before a worker receives the task.
        # No retry here: a caller can inspect the durable request after timeouts.
        try:
            enqueue_transcription(row)
        except (OperationalError, OSError):
            TranscriptionRequest.objects.filter(pk=row.pk, status="pending").update(
                status="fail",
                logs="Could not enqueue transcription; retry from inspiration",
            )
            raise Unavailable("Could not enqueue transcription") from None
        return Response(_transcription_state(media), status=202)
