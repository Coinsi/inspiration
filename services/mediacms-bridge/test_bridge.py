"""Exercise the real DRF permission/response stack with upstream model-shaped doubles.

Run in an isolated environment with Django, djangorestframework, pysubs2, kombu,
and pytest. These are unit contracts; actual MediaCMS/Celery integration is a
separate deployed-service acceptance test.
"""

import hashlib
import importlib
import json
import sys
import types
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import django
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        SECRET_KEY="isolated-test-only",
        INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes", "rest_framework"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSPIRATION_SERVICE_TOKEN="a" * 40,
        PORTAL_WORKFLOW="private",
        USE_WHISPER_TRANSCRIBE=True,
        ALLOWED_HOSTS=["testserver"],
        REST_FRAMEWORK={"UNAUTHENTICATED_USER": None},
    )
    django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.http import Http404  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    package = types.ModuleType("mediacms_bridge")
    package.__path__ = [str(Path(__file__).parent)]
    monkeypatch.setitem(sys.modules, "mediacms_bridge", package)
    for name in (
        "files",
        "files.models",
        "files.models.subtitle",
        "files.models.utils",
        "files.serializers",
        "files.tasks",
    ):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    media_model = SimpleNamespace(objects=Mock())
    transcription_model = SimpleNamespace(objects=Mock())
    sys.modules["files.models"].Media = media_model
    sys.modules["files.models"].EncodeProfile = SimpleNamespace(objects=Mock())
    sys.modules["files.models.subtitle"].TranscriptionRequest = transcription_model
    sys.modules["files.models.utils"].PREVIEW_PROFILE_NAME = "preview"
    sys.modules["files.serializers"].MediaSerializer = lambda media, context: SimpleNamespace(
        data={
            "friendly_token": media.friendly_token,
            "state": media.state,
            "encoding_status": "pending",
        }
    )
    tasks = sys.modules["files.tasks"]
    tasks.whisper_transcribe = SimpleNamespace(apply_async=Mock())
    sys.modules["files"].tasks = tasks
    integration_tasks = types.ModuleType("mediacms_bridge.tasks")
    integration_tasks.enqueue_initialization = Mock()
    integration_tasks.enqueue_encodings = Mock()
    integration_tasks.enqueue_transcription = Mock()
    monkeypatch.setitem(sys.modules, "mediacms_bridge.tasks", integration_tasks)
    recovery = types.ModuleType("mediacms_bridge.recovery")
    recovery.available_lock = lambda *_: nullcontext(True)
    recovery.recover_media = Mock()
    recovery.new_speech_delivery = lambda: "inspiration-task:inspiration-unit-test:0"
    monkeypatch.setitem(sys.modules, "mediacms_bridge.recovery", recovery)
    monkeypatch.delitem(sys.modules, "mediacms_bridge.views", raising=False)
    view = importlib.import_module("mediacms_bridge.views")
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "TEMP_DIRECTORY", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "FILE_UPLOAD_TEMP_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "INSPIRATION_DISK_RESERVE_BYTES", 0, raising=False)
    monkeypatch.setattr(view.transaction, "atomic", nullcontext)
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(view, "connection", SimpleNamespace(cursor=lambda: cursor))
    return view, integration_tasks, cursor


def request(method, path, data=None, *, authenticate=True, token="a" * 40, format="json"):
    factory = APIRequestFactory()
    req = getattr(factory, method)(path, data=data, format=format)
    if authenticate:
        force_authenticate(
            req, user=SimpleNamespace(pk=1, is_active=True), token=SimpleNamespace(key=token)
        )
    return req


def collection(first=None, all_rows=()):
    query = Mock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.select_related.return_value = query
    query.first.return_value = first
    query.all.return_value = all_rows
    return query


def encoding_media(view, rows, *, dispatch="submitted", profiles=(1, 2, 3)):
    return SimpleNamespace(
        pk=7,
        description="inspiration:source:5"
        + view.PIPELINE_MARKER
        + json.dumps({"dispatch": dispatch, "profiles": list(profiles)}),
        media_type="video",
        video_height=720,
        encodings=collection(all_rows=rows),
    )


def encoding(pk, profile, status, *, chunk=False):
    return SimpleNamespace(pk=pk, profile_id=profile, status=status, chunk=chunk)


@pytest.mark.parametrize("second_status", ["pending", "running"])
def test_first_successful_profile_does_not_complete_other_active_profiles(bridge, second_status):
    view, _, _ = bridge
    media = encoding_media(view, [encoding(1, 1, "success"), encoding(2, 2, second_status)])
    progress = view._encoding_progress(media)
    assert not progress["complete"]
    assert progress["expected"] == 3 and progress["terminal"] == 1


def test_missing_profile_after_dispatch_is_a_failure_instead_of_waiting_forever(bridge):
    view, _, _ = bridge
    media = encoding_media(view, [encoding(1, 1, "success"), encoding(2, 2, "success", chunk=True)])
    progress = view._encoding_progress(media)
    assert progress["complete"] and progress["status"] == "fail"
    assert progress["dispatch"] == "failed"
    assert progress["pending"] == progress["running"] == 0
    assert progress["terminal"] == 1


def test_all_profiles_must_be_submitted_and_terminal_and_failure_stays_visible(bridge):
    view, _, _ = bridge
    rows = [encoding(1, 1, "success"), encoding(2, 2, "success"), encoding(3, 3, "fail")]
    submitting = encoding_media(view, rows, dispatch="submitting")
    assert not view._encoding_progress(submitting)["complete"]
    media = encoding_media(view, rows)
    progress = view._encoding_progress(media)
    assert progress["complete"] and progress["status"] == "fail" and progress["failed"] == 1
    rows.append(encoding(4, 3, "success"))
    progress = view._encoding_progress(media)
    assert progress["complete"] and progress["status"] == "success" and progress["failed"] == 0


def test_initialization_without_snapshot_cannot_be_mistaken_for_completion(bridge):
    view, _, _ = bridge
    media = encoding_media(view, [encoding(1, 1, "success")])
    media.description = "inspiration:source:5"
    assert not view._encoding_progress(media)["complete"]


def test_live_job_lease_blocks_cleanup_even_if_database_row_looks_terminal(bridge, monkeypatch):
    view, _, _ = bridge
    recovery = sys.modules["mediacms_bridge.recovery"]
    monkeypatch.setattr(
        recovery, "available_lock", lambda kind, identifier: nullcontext(kind != "encode")
    )
    media = encoding_media(view, [encoding(1, 1, "success")], profiles=(1,))
    progress = view._encoding_progress(media)
    assert not progress["complete"] and progress["running"] == 1
    monkeypatch.setattr(recovery, "available_lock", lambda *_: nullcontext(False))
    media.transcriptionrequests = collection(SimpleNamespace(pk=7, status="fail"))
    assert view._transcription_state(media)["status"] == "running"


def test_dispatch_snapshots_eligible_profiles_and_disables_chunk_scheduling(bridge, monkeypatch):
    view, _, _ = bridge
    profiles = [
        SimpleNamespace(pk=1, name="h264-360", resolution=360),
        SimpleNamespace(pk=2, name="h264-720", resolution=720),
        SimpleNamespace(pk=3, name="preview", resolution=None),
        SimpleNamespace(pk=4, name="h264-1080", resolution=1080),
    ]
    view.EncodeProfile.objects.filter.return_value = profiles
    monkeypatch.setattr(settings, "MINIMUM_RESOLUTIONS_TO_ENCODE", [360], raising=False)
    media = encoding_media(view, [])

    def encode_actual(**kwargs):
        snapshot = view._pipeline_snapshot(media)
        assert snapshot["profiles"] == [1, 2, 3] and snapshot["dispatch"] == "submitting"
        assert kwargs["chunkize"] is False
        assert [profile.pk for profile in kwargs["profiles"]] == [1, 2, 3]

    view._dispatch_encodings(media, encode_actual)
    assert view._pipeline_snapshot(media)["dispatch"] == "submitted"
    assert view.Media.objects.filter.return_value.update.call_count == 2


def test_dispatch_failure_is_explicit_after_already_started_tasks_stop(bridge):
    view, _, _ = bridge
    rows = [encoding(1, 1, "running")]
    media = encoding_media(view, rows, dispatch="failed")
    assert not view._encoding_progress(media)["complete"]
    rows[0].status = "success"
    progress = view._encoding_progress(media)
    assert progress["complete"] and progress["status"] == "fail"


def test_encoding_retry_reuses_snapshot_and_waits_for_commit(bridge, monkeypatch):
    view, tasks, _ = bridge
    media = encoding_media(view, [encoding(1, 1, "fail")], profiles=(1,))
    media.encode = Mock()
    monkeypatch.setattr(view.EncodeView, "media", lambda *args, **kwargs: media)
    view.EncodeProfile.objects.filter.return_value = [SimpleNamespace(pk=1)]
    callbacks = []
    monkeypatch.setattr(view.transaction, "on_commit", callbacks.append)
    endpoint = view.EncodeView.as_view()
    assert endpoint(request("post", "/", {}), token="abc").status_code == 202
    assert view._pipeline_snapshot(media)["dispatch"] == "submitting"
    media.encode.assert_not_called()
    assert endpoint(request("post", "/", {}), token="abc").status_code == 202
    assert len(callbacks) == 1
    callbacks[0]()
    tasks.enqueue_encodings.assert_called_once_with(
        media, view.EncodeProfile.objects.filter.return_value
    )
    assert view._pipeline_snapshot(media)["dispatch"] == "submitted"


def test_unauthenticated_and_wrong_service_token_are_rejected(bridge):
    view, _, _ = bridge
    endpoint = view.ManifestView.as_view()
    assert endpoint(request("get", "/", authenticate=False), token="abc").status_code == 401
    assert endpoint(request("get", "/", token="b" * 40), token="abc").status_code == 403


def test_even_superuser_must_own_media_and_private_state(bridge, monkeypatch):
    view, _, _ = bridge
    seen = []

    def get_object(manager, **filters):
        seen.append(filters)
        if filters["friendly_token"] == "other":
            raise Http404()
        return SimpleNamespace(state="public")

    monkeypatch.setattr(view, "get_object_or_404", get_object)
    endpoint = view.ManifestView.as_view()
    assert endpoint(request("get", "/"), token="other").status_code == 404
    assert seen[0] == {"friendly_token": "other", "user_id": 1}
    assert endpoint(request("get", "/"), token="mine").status_code == 403


def test_download_is_selected_by_key_and_streamed(bridge, monkeypatch, tmp_path):
    view, _, _ = bridge
    output = tmp_path / "preview.mp4"
    output.write_bytes(b"private video")
    monkeypatch.setattr(view.OutputView, "media", lambda *_: object())
    monkeypatch.setattr(
        view,
        "_outputs",
        lambda _: {
            "preview": ({"mime": "video/mp4"}, output),
        },
    )
    endpoint = view.OutputView.as_view()
    missing = endpoint(request("get", "/"), token="abc", output_key="../../secret")
    assert missing.status_code == 404
    response = endpoint(request("get", "/"), token="abc", output_key="preview")
    assert response.streaming
    assert b"".join(response.streaming_content) == b"private video"
    assert response["Cache-Control"] == "private, no-store"
    response.close()


def test_upload_rejects_bad_hash_before_database_write(bridge):
    view, _, cursor = bridge
    req = request(
        "post",
        "/",
        {
            "upload_key": "run123",
            "source_sha256": "0" * 64,
            "media_file": SimpleUploadedFile("source.mp4", b"video"),
        },
        format="multipart",
    )
    response = view.UploadView.as_view()(req)
    assert response.status_code == 400
    view.Media.objects.create.assert_not_called()
    cursor.execute.assert_not_called()


def test_new_media_initializes_only_after_commit(bridge):
    from django.db.transaction import Atomic

    view, tasks, _ = bridge
    initialize = Mock()
    media = SimpleNamespace(
        pk=7,
        description="inspiration:hash:5",
        media_init=initialize,
        media_type="video",
        encode=Mock(),
    )
    media.save = lambda: media.media_init()  # actual upstream post_save contract
    with Atomic("default", savepoint=True, durable=False):
        view._save_new_media(media)
        initialize.assert_not_called()
        tasks.enqueue_initialization.assert_not_called()
        assert media._skip_admin_notification
    initialize.assert_not_called()
    tasks.enqueue_initialization.assert_called_once_with(media)


def test_rolled_back_upload_does_not_start_encoding(bridge):
    from django.db.transaction import Atomic

    view, tasks, _ = bridge
    initialize = Mock()
    media = SimpleNamespace(
        pk=7,
        description="inspiration:hash:5",
        media_init=initialize,
        media_type="video",
        encode=Mock(),
    )
    media.save = lambda: media.media_init()
    with pytest.raises(RuntimeError, match="rollback"):
        with Atomic("default", savepoint=True, durable=False):
            view._save_new_media(media)
            raise RuntimeError("rollback")
    initialize.assert_not_called()
    tasks.enqueue_initialization.assert_not_called()


def test_audio_initialization_runs_preview_before_publishing_completion(bridge, monkeypatch):
    view, _, _ = bridge
    calls = []
    audio_module = types.ModuleType("mediacms_bridge.audio_preview")
    audio_module.build_audio_preview = lambda media: calls.append("audio")
    monkeypatch.setitem(sys.modules, "mediacms_bridge.audio_preview", audio_module)
    media = SimpleNamespace(media_type="audio", media_init=lambda: calls.append("native"))
    monkeypatch.setattr(view, "_record_pipeline", lambda *args: calls.append("published"))
    view.initialize_media(media)
    assert calls == ["native", "audio", "published"]


def test_audio_preview_failure_does_not_publish_completion(bridge, monkeypatch):
    view, _, _ = bridge
    audio_module = types.ModuleType("mediacms_bridge.audio_preview")
    audio_module.build_audio_preview = Mock(side_effect=ValueError("audio failed"))
    monkeypatch.setitem(sys.modules, "mediacms_bridge.audio_preview", audio_module)
    record = Mock()
    monkeypatch.setattr(view, "_record_pipeline", record)
    with pytest.raises(ValueError, match="audio failed"):
        view.initialize_media(SimpleNamespace(media_type="audio", media_init=lambda: None))
    record.assert_not_called()


def test_failed_audio_retry_restarts_supervised_initialization_after_commit(bridge, monkeypatch):
    view, tasks, _ = bridge
    media = encoding_media(view, [encoding(1, 1, "fail")], dispatch="failed", profiles=())
    media.media_type = "audio"
    monkeypatch.setattr(view.EncodeView, "media", staticmethod(lambda *args, **kwargs: media))
    callbacks = []
    monkeypatch.setattr(view.transaction, "on_commit", callbacks.append)
    result = view.EncodeView.as_view()(request("post", "/encode", {}), token="audio")
    assert result.status_code == 202
    assert view._pipeline_snapshot(media)["dispatch"] == "initializing"
    tasks.enqueue_initialization.assert_not_called()
    callbacks[0]()
    tasks.enqueue_initialization.assert_called_once_with(media)


def test_upload_retry_returns_same_private_identity_without_creation(bridge):
    view, _, cursor = bridge
    digest = hashlib.sha256(b"video").hexdigest()
    key_digest = hashlib.sha256(b"run123").hexdigest()
    media = SimpleNamespace(
        user_id=1,
        state="private",
        description=f"inspiration:{digest}:5",
        friendly_token="ins_" + key_digest[:48],
    )
    view.Media.objects.filter.return_value.first.return_value = media
    req = request(
        "post",
        "/",
        {
            "upload_key": "run123",
            "source_sha256": digest,
            "media_file": SimpleUploadedFile("source.mp4", b"video"),
        },
        format="multipart",
    )
    response = view.UploadView.as_view()(req)
    assert response.status_code == 200
    assert response.data["friendly_token"] == media.friendly_token
    cursor.execute.assert_called_once()
    assert cursor.execute.call_args[0][0] == "SELECT pg_advisory_xact_lock(%s)"
    view.Media.objects.create.assert_not_called()


def test_upload_key_cannot_rebind_to_another_source(bridge):
    view, _, _ = bridge
    view.Media.objects.filter.return_value.first.return_value = SimpleNamespace(
        user_id=1,
        state="private",
        description="inspiration:older-source:5",
    )
    req = request(
        "post",
        "/",
        {
            "upload_key": "run123",
            "source_sha256": hashlib.sha256(b"video").hexdigest(),
            "media_file": SimpleUploadedFile("source.mp4", b"video"),
        },
        format="multipart",
    )
    assert view.UploadView.as_view()(req).status_code == 409
    view.Media.objects.create.assert_not_called()


def test_transcription_pending_is_idempotent_and_failure_retry_dispatches(bridge, monkeypatch):
    view, tasks, _ = bridge
    row = SimpleNamespace(pk=7, status="pending", logs="", save=Mock())
    media = SimpleNamespace(media_type="video", transcriptionrequests=collection(row))
    monkeypatch.setattr(view.TranscriptionView, "media", lambda *_, **kw: media)
    endpoint = view.TranscriptionView.as_view()
    response = endpoint(request("post", "/", {}), token="abc")
    assert response.status_code == 200
    assert response.data["status"] == "pending"
    tasks.enqueue_transcription.assert_not_called()
    row.status = "fail"
    response = endpoint(request("post", "/", {"retry": True}), token="abc")
    assert response.status_code == 202
    assert row.status == "pending"
    tasks.enqueue_transcription.assert_called_once_with(row)


def test_failed_dispatch_marks_request_failed(bridge, monkeypatch):
    view, tasks, _ = bridge
    row = SimpleNamespace(pk=7, status="fail", logs="", save=Mock())
    media = SimpleNamespace(media_type="video", transcriptionrequests=collection(row))
    monkeypatch.setattr(view.TranscriptionView, "media", lambda *_, **kw: media)
    tasks.enqueue_transcription.side_effect = view.OperationalError("broker secret")
    response = view.TranscriptionView.as_view()(request("post", "/", {"retry": True}), token="abc")
    assert response.status_code == 503
    assert "secret" not in str(response.data)
    view.TranscriptionRequest.objects.filter.assert_called_with(pk=7, status="pending")
    view.TranscriptionRequest.objects.filter.return_value.update.assert_called_once()


def test_real_vtt_is_returned_as_timestamped_cues(bridge, tmp_path):
    view, _, _ = bridge
    subtitles = tmp_path / "captions.vtt"
    subtitles.write_text(
        "WEBVTT\n\n00:00:01.250 --> 00:00:03.500\n你好，世界\n\n"
        "00:00:04.000 --> 00:00:05.000\n第二句\n",
        encoding="utf-8",
    )
    media = SimpleNamespace(
        friendly_token="abc",
        transcriptionrequests=collection(SimpleNamespace(pk=8, status="success")),
        subtitles=collection(
            SimpleNamespace(pk=12, subtitle_file=SimpleNamespace(path=str(subtitles)))
        ),
    )
    data = view._transcription_with_cues(media)
    assert data["cues"] == [
        {"start_ms": 1250, "end_ms": 3500, "text": "你好，世界", "language": "und"},
        {"start_ms": 4000, "end_ms": 5000, "text": "第二句", "language": "und"},
    ]
    assert data["subtitle"]["url"].endswith("/files/subtitle-12")


def test_success_without_subtitle_file_does_not_claim_ready(bridge):
    view, _, _ = bridge
    media = SimpleNamespace(
        transcriptionrequests=collection(SimpleNamespace(pk=8, status="success")),
        subtitles=collection(None),
    )
    with pytest.raises(view.Unavailable):
        view._transcription_with_cues(media)
