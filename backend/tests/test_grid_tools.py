"""Grid crops: real pixels, source isolation, permissions and retry safety."""

# ruff: noqa: F811
import io
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.core.errors import CapabilityUnsupported
from app.models.generation import Generation, GenerationJob
from app.models.identity import Membership
from app.models.storage import Blob
from app.modules.generation import grid_tools
from app.storage import cas
from tests.test_media_workflow import studio  # noqa: F401


def pixels(size=(7, 5)):
    image = Image.new("RGBA", size)
    for y in range(size[1]):
        for x in range(size[0]):
            image.putpixel((x, y), (x * 20, y * 30, 50, 100 + x))
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return image, stream.getvalue()


def test_grid_preserves_every_pixel_alpha_and_odd_edges():
    original, raw = pixels()
    assembled = Image.new("RGBA", original.size)
    results = grid_tools.crop_grid(raw, 2, 3, list(range(6)))
    for index, box, encoded in results:
        with Image.open(io.BytesIO(encoded)) as crop:
            assert crop.size == (box[2] - box[0], box[3] - box[1])
            assembled.paste(crop, box[:2])
        assert 0 <= index < 6
    assert assembled.tobytes() == original.tobytes()
    with pytest.raises(CapabilityUnsupported):
        grid_tools.crop_grid(raw, 6, 3, [0])
    with pytest.raises(CapabilityUnsupported):
        grid_tools.crop_grid(b"not an image", 2, 2, [0])


def test_grid_obeys_exif_orientation_and_rejects_animation():
    original = Image.new("RGB", (12, 8), "red")
    exif = original.getexif()
    exif[274] = 6
    stream = io.BytesIO()
    original.save(stream, "JPEG", exif=exif)
    assert [box for _, box, _ in grid_tools.crop_grid(stream.getvalue(), 2, 1, [0, 1])] == [
        (0, 0, 8, 6),
        (0, 6, 8, 12),
    ]
    stream = io.BytesIO()
    original.save(stream, "GIF", save_all=True, append_images=[Image.new("RGB", (12, 8), "blue")])
    with pytest.raises(CapabilityUnsupported):
        grid_tools.crop_grid(stream.getvalue(), 2, 2, [0])


def upload(client, ids):
    base = f"/api/v1/projects/{ids['project']}"
    _, raw = pixels()
    response = client.post(
        f"{base}/media/shot/{ids['shot']}/upload", files={"file": ("grid.png", raw, "image/png")}
    )
    assert response.status_code == 200, response.text
    return base, response.json()


def test_split_api_sources_retry_concurrency_and_permissions(studio):
    client, sessions, ids = studio
    base, source = upload(client, ids)
    path = f"{base}/generations/{source['id']}/grid-split"
    options = {"rows": 2, "columns": 3, "cells": [5, 0, 2], "request_key": str(uuid.uuid4())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(path, json=options), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    results = responses[0].json()
    assert [g["id"] for g in results] == [g["id"] for g in responses[1].json()]
    assert [g["input_refs"]["cell_index"] for g in results] == [0, 2, 5]
    assert all(g["input_refs"]["source_generation_id"] == source["id"] for g in results)
    assert all(not g["is_selected"] and g["cost_points"] == 0 for g in results)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Generation)) == 4
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 2
        for g in results:
            box = g["input_refs"]["crop_box"]
            blob = db.get(Blob, g["output_blob_hash"])
            image = Image.open(io.BytesIO(cas.read_bytes(blob)))
            assert image.getpixel((0, 0)) == (box[0] * 20, box[1] * 30, 50, 100 + box[0])
    assert client.post(path, json={**options, "columns": 4}).status_code == 409
    assert (
        client.post(path.replace(ids["project"], str(ids["other"])), json=options).status_code
        == 403
    )
    with sessions() as db:
        foreign = Generation(
            project_id=ids["other"],
            job_id=uuid.UUID(source["job_id"]),
            target_type="shot",
            target_id=uuid.UUID(ids["shot"]),
            provider="local",
            output_type="image",
            output_blob_hash=source["output_blob_hash"],
        )
        db.add(foreign)
        db.flush()
        foreign_id = str(foreign.id)
        db.commit()
    assert client.post(path.replace(source["id"], foreign_id), json=options).status_code == 404
    with sessions() as db:
        member = db.scalar(
            select(Membership).where(Membership.project_id == uuid.UUID(ids["project"]))
        )
        member.role = "viewer"
        db.commit()
    assert client.post(path, json=options).status_code == 403


def test_split_rejects_bad_options_and_rolls_back_partial_failure(studio, monkeypatch):
    client, sessions, ids = studio
    base, source = upload(client, ids)
    path = f"{base}/generations/{source['id']}/grid-split"
    options = {"rows": 2, "columns": 2, "cells": [0, 1, 2, 3], "request_key": str(uuid.uuid4())}
    for change in [
        dict(rows=0),
        dict(columns=5),
        dict(cells=[]),
        dict(cells=[4]),
        dict(cells=[1, 1]),
        dict(cells=[-1]),
        dict(rows=1, columns=1, cells=[0]),
    ]:
        assert client.post(path, json={**options, **change}).status_code == 422
    write = cas.put_bytes
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise CapabilityUnsupported("模拟存储失败")
        return write(*args, **kwargs)

    monkeypatch.setattr(cas, "put_bytes", fail_second)
    assert client.post(path, json=options).status_code == 422
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Generation)) == 1
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
    monkeypatch.setattr(cas, "put_bytes", write)
    response = client.post(path, json=options)
    assert response.status_code == 200 and len(response.json()) == 4
    with sessions() as db:
        generation = db.get(Generation, uuid.UUID(source["id"]))
        generation.output_type = "video"
        db.commit()
    assert client.post(path, json={**options, "request_key": str(uuid.uuid4())}).status_code == 422
