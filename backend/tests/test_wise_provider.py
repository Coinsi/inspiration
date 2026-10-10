import uuid

import httpx
import pytest

from app.core.errors import CapabilityUnsupported, ProviderError
from app.modules.generation.media_engine import Canceled
from app.modules.library.providers.wise import WiseClient


@pytest.mark.parametrize(
    "kind,mime,extension", [("image", "image/png", ".png"), ("audio", "audio/mp4", ".m4a")]
)
def test_media_upload_preserves_kind_and_cas_mime(tmp_path, kind, mime, extension):
    path = tmp_path / "cas-hash-without-extension"
    path.write_bytes(b"test content")
    source_id = uuid.uuid4()

    def handle(request):
        body = request.read()
        assert f'filename="{source_id}{extension}"'.encode() in body
        assert f"Content-Type: {mime}".encode() in body
        assert f"\r\n\r\n{kind}\r\n".encode() in body
        return httpx.Response(202, json={"status": "queued"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        WiseClient("http://wise.test", "token", client=transport).submit(
            uuid.uuid4(),
            source_id,
            uuid.uuid4(),
            path,
            ["audio"] if kind == "audio" else ["visual"],
            media_type=kind,
            mime_type=mime,
        )


def test_remote_errors_and_redirects_do_not_leak_tokens():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://elsewhere.invalid/steal"})

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        headers={"X-Other-Secret": "do-not-send"},
    ) as transport:
        client = WiseClient("http://wise.test", "private-token", client=transport)
        with pytest.raises(ProviderError) as error:
            client.get_job(uuid.uuid4())
    assert "private-token" not in str(error.value)
    assert len(seen) == 1
    assert "X-Other-Secret" not in seen[0].headers


def test_reference_region_is_forwarded_as_a_multipart_field():
    import json

    box = {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}

    def handle(request):
        body = request.read()
        assert b'name="bbox"' in body and json.dumps(box).encode() in body
        assert b'name="source_ids"' in body
        return httpx.Response(200, json={"items": []})

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        WiseClient("http://wise.test", "test-token", client=transport).search_media(
            uuid.uuid4(),
            b"test image",
            modality="object",
            source_ids=[str(uuid.uuid4())],
            limit=20,
            bbox=box,
        )


def test_missing_job_is_distinct_from_upstream_failure():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as transport:
        assert (
            WiseClient("http://wise.test", "token", client=transport).get_job(uuid.uuid4()) is None
        )
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as transport:
        with pytest.raises(ProviderError):
            WiseClient("http://wise.test", "token", client=transport).get_job(uuid.uuid4())


def test_busy_indexer_has_a_clear_message_without_forwarding_arbitrary_diagnostics():
    for detail, is_busy in [
        ("WISE is processing another task; retry search shortly", True),
        ("private upstream diagnostic with a secret", False),
    ]:
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda r, message=detail: httpx.Response(503, json={"detail": message})
            )
        ) as transport:
            with pytest.raises(ProviderError) as error:
                WiseClient("http://wise.test", "token", client=transport).search(
                    uuid.uuid4(), {"query": "blue", "modality": "visual"}
                )
        assert (error.value.detail.get("reason") == "busy") is is_busy
        assert "private upstream" not in error.value.message
        if is_busy:
            assert "稍后" in error.value.message


@pytest.mark.parametrize(
    "detail,reason",
    [
        (
            "Optional index fingerprint is outdated or unknown; "
            "rebuild this project's index before searching",
            "reindex_required",
        ),
        (
            "Optional model fingerprint changed during search; rebuild the index before retrying",
            "reindex_required",
        ),
        (
            "Optional model acceptance is unavailable; complete the offline probe before searching",
            "model_validation_required",
        ),
        ({"private_path": "/private/secret"}, None),
        ("Optional index fingerprint is outdated or unknown; /private/secret", None),
    ],
)
def test_stale_models_require_operator_action_without_leaking_diagnostics(detail, reason):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(503, json={"detail": detail}))
    ) as transport:
        with pytest.raises(ProviderError) as error:
            WiseClient("http://wise.test", "token", client=transport).search(
                uuid.uuid4(), {"query": "cup", "modality": "object"}
            )
    assert error.value.detail.get("reason") == reason
    assert error.value.detail["retryable"] is (reason is None)
    assert "/private/secret" not in error.value.message
    if reason == "reindex_required":
        assert "重新建立索引" in error.value.message


@pytest.mark.parametrize(
    "code,phrase", [("reference_no_object", "未检测到"), ("reference_region_no_match", "重新框选")]
)
def test_expected_reference_failure_is_safe_actionable_input_error(code, phrase):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                422, json={"detail": {"code": code, "message": "/private/secret-key"}}
            )
        )
    ) as transport:
        with pytest.raises(CapabilityUnsupported) as error:
            WiseClient("http://wise.test", "private-token", client=transport).search_media(
                uuid.uuid4(), b"fixture", modality="object", source_ids=[], limit=20
            )
    assert error.value.http_status == 422
    assert error.value.detail == {"retryable": False, "reason": code}
    assert phrase in error.value.message
    assert "/private" not in error.value.message and "secret-key" not in error.value.message


@pytest.mark.parametrize(
    "detail",
    [
        {"code": "reference_no_object /private/key"},
        {"code": ["reference_no_object"]},
        "/private/key",
        None,
    ],
)
def test_unknown_input_diagnostics_stay_generic_and_private(detail):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(422, json={"detail": detail}))
    ) as transport:
        with pytest.raises(ProviderError) as error:
            WiseClient("http://wise.test", "token", client=transport).search_media(
                uuid.uuid4(), b"fixture", modality="object", source_ids=[], limit=20
            )
    assert "/private" not in error.value.message
    assert error.value.detail == {"retryable": False}


def test_reference_error_reaches_public_api_as_422_with_actionable_message():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.errors import AppError, app_error_handler

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    @app.get("/query")
    def query():
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    422,
                    json={
                        "detail": {"code": "reference_region_no_match", "message": "/private/token"}
                    },
                )
            )
        ) as transport:
            return WiseClient("http://wise.test", "private-token", client=transport).search_media(
                uuid.uuid4(), b"reference", modality="object", source_ids=[], limit=20
            )

    with TestClient(app) as client:
        response = client.get("/query")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "CAPABILITY_UNSUPPORTED"
    assert error["detail"] == {"retryable": False, "reason": "reference_region_no_match"}
    assert "重新框选" in error["message"] and "/private" not in response.text


@pytest.mark.parametrize(
    "base", ["file:///secret", "http://user:pass@wise.test", "http://wise.test?key=secret"]
)
def test_invalid_server_address(base):
    with pytest.raises(ProviderError):
        WiseClient(base, "token")


def test_proxy_upload_can_stop_while_streaming(tmp_path):
    source = tmp_path / "proxy.mp4"
    source.write_bytes(b"x" * (128 * 1024))
    calls = 0

    def canceled():
        nonlocal calls
        calls += 1
        return calls >= 2

    def handler(request):
        request.read()
        raise AssertionError("An interrupted upload must not complete")

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        client = WiseClient("http://wise.test", "private-token", client=transport)
        with pytest.raises(Canceled):
            client.submit(
                uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), source, ["visual"], canceled=canceled
            )


@pytest.mark.parametrize("anchored", [False, True])
def test_restore_and_delete_generation_anchors_use_the_documented_wire_fields(tmp_path, anchored):
    source = tmp_path / "proxy.mp4"
    source.write_bytes(b"synthetic test payload")
    project_id, source_id, job_id, anchor = [uuid.uuid4() for _ in range(4)]
    seen = []

    def handler(request):
        seen.append((request, request.read()))
        return httpx.Response(202, json={"status": "queued"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        client = WiseClient("http://wise.test", "test-token", client=transport)
        client.submit(
            project_id,
            source_id,
            job_id,
            source,
            ["visual"],
            restore_after_job_id=anchor if anchored else None,
        )
        client.delete(project_id, source_id, job_id, source_job_id=anchor if anchored else None)
    post, delete = seen
    assert post[0].method == "POST" and delete[0].method == "DELETE"
    assert (b'name="restore_after_job_id"' in post[1]) is anchored
    if anchored:
        assert str(anchor).encode() in post[1]
        assert delete[0].url.params["source_job_id"] == str(anchor)
    else:
        assert "source_job_id" not in delete[0].url.params
    assert delete[0].url.params["job_id"] == str(job_id)
