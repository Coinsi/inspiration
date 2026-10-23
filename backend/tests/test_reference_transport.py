import base64

import pytest

from app.adapters.contracts import ReferenceImage
from app.adapters.generation.reference_transport import image_file
from app.core.config import settings
from app.core.errors import CapabilityUnsupported
from app.modules.media.generation_reference import public_base, reference_origin, reference_url


def test_local_jpeg_upload_keeps_correct_type_and_extension():
    ref = ReferenceImage(
        blob_hash="a" * 64, data_url="data:image/jpeg;base64," + base64.b64encode(b"jpeg").decode()
    )
    assert image_file(ref) == ("reference.jpg", b"jpeg", "image/jpeg")


@pytest.mark.parametrize(
    "value",
    [
        "https://host/a.png",
        "data:image/svg+xml;base64,YQ==",
        "data:image/png;base64,invalid!",
        "data:image/png;base64,",
    ],
)
def test_invalid_input_does_not_silently_become_text_to_video(value):
    with pytest.raises(CapabilityUnsupported):
        image_file(ReferenceImage(blob_hash="a" * 64, data_url=value))


def test_configured_ingress_allows_local_submission_without_moving_storage(monkeypatch):
    monkeypatch.setattr(
        settings, "generation_reference_public_base_url", "https://media.example.com"
    )
    monkeypatch.setattr(settings, "secret_key", "test-key-" * 8)
    with reference_origin("http://127.0.0.1:8000"):
        assert reference_url("a" * 64).startswith(
            "https://media.example.com/api/v1/generation-references/"
        )


@pytest.mark.parametrize(
    "origin",
    ["http://media.example.com", "https://127.0.0.1", "https://user:pass@media.example.com"],
)
def test_bad_ingress_is_not_advertised(monkeypatch, origin):
    monkeypatch.setattr(settings, "generation_reference_public_base_url", origin)
    assert not public_base()
