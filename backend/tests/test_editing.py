import base64
import io

import pytest
from PIL import Image
from pydantic import ValidationError

from app.adapters.contracts import GenerationRequest, ReferenceImage
from app.adapters.generation.gpt_image import GptImageProvider
from app.core.errors import CapabilityUnsupported
from app.modules.generation.editing import draw_mask
from app.modules.generation.schemas import MaskStroke


def test_mask_transparency_and_erase_are_source_aligned():
    brush = MaskStroke(points=[(0.2, 0.5), (0.8, 0.5)], radius=0.1)
    erase = MaskStroke(points=[(0.5, 0.5)], radius=0.04, erase=True)
    mask = Image.open(io.BytesIO(draw_mask((200, 100), [brush, erase])))
    assert mask.mode == "RGBA" and mask.size == (200, 100)
    assert mask.getpixel((50, 50))[3] == 0
    assert mask.getpixel((100, 50))[3] == 255
    assert mask.getpixel((0, 0))[3] == 255
    with pytest.raises(CapabilityUnsupported):
        draw_mask((200, 100), [erase])
    with pytest.raises(CapabilityUnsupported):
        draw_mask((8192, 8192), [brush])
    for points in [[(-0.1, 0)], [(float("nan"), 0)], [(1.1, 0)]]:
        with pytest.raises(ValidationError):
            MaskStroke(points=points, radius=0.1)


def test_multipart_mask_is_separate_from_reference_image(monkeypatch):
    provider = GptImageProvider(token="test-token", config={"model": "gpt-image-2"})
    source = ReferenceImage(
        blob_hash="source", data_url="data:image/png;base64," + base64.b64encode(b"SOURCE").decode()
    )
    mask = ReferenceImage(
        blob_hash="mask",
        role="mask",
        data_url="data:image/png;base64," + base64.b64encode(b"MASK").decode(),
    )
    captured = []
    monkeypatch.setattr(
        provider, "_http", lambda req: captured.append(req) or {"data": [{"b64_json": "eA=="}]}
    )
    provider.submit(
        GenerationRequest(request_type="image", prompt="change hat", references=[source], mask=mask)
    )
    assert len(captured) == 1
    body = captured[0].data
    assert body.count(b'name="image[]"') == 1 and body.count(b'name="mask"') == 1
    assert b"SOURCE\r\n" in body and b"MASK\r\n" in body
    assert "inpaint" not in GptImageProvider(config={"mask_editing": False}).capabilities().features
