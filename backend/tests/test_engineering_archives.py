"""Archive media bytes are complete, verified and independent of original CAS paths."""

# ruff: noqa: F811
import io
import uuid
import zipfile
from contextlib import contextmanager

import pytest

from app.core.errors import CapabilityUnsupported
from app.modules.engineering import archives
from tests.test_engineering import accept
from tests.test_engineering_review import draft, preview, ready
from tests.test_media_workflow import (
    post_ok,
    studio,  # noqa: F401
)


def test_archive_worker_resumes_verified_sources_after_packing_cancel(
    studio, tmp_path, monkeypatch
):
    from app.modules.engineering import service
    from app.modules.generation.media_engine import Canceled

    client, sessions, _, path, tid, url = ready(studio)
    change = draft(client, path, tid, url)
    assert preview(client, path, change)["preview"]["status"] == "succeeded"
    accept(client, path, change)
    work_id = uuid.UUID(path.split("/works/")[1].split("/")[0])
    stopped = False

    def progress(event):
        nonlocal stopped
        if event["phase"] == "packing":
            stopped = True

    with sessions() as db:
        snapshot = service.snapshot_at(db, work_id, 1)
        with pytest.raises(Canceled):
            archives.build(
                db,
                work_id,
                snapshot,
                checkpoint_dir=tmp_path / "transfer",
                canceled=lambda: stopped,
                progress=progress,
            )
        completed = [p for p in (tmp_path / "transfer").iterdir() if len(p.name) == 64]
        assert completed
        original = archives.cas.open_range
        requested = []

        @contextmanager
        def track(blob, *args, **kwargs):
            requested.append(blob.hash)
            with original(blob, *args, **kwargs) as stream:
                yield stream

        monkeypatch.setattr(archives.cas, "open_range", track)
        result = archives.build(db, work_id, snapshot, checkpoint_dir=tmp_path / "transfer")
        try:
            envelope = archives.inspect(result)
            assert envelope["media"]
            assert not {p.name for p in completed}.intersection(requested)
            canceled = False

            def cancel_validation(event):
                nonlocal canceled
                canceled = True

            with pytest.raises(Canceled):
                archives.inspect(result, canceled=lambda: canceled, progress=cancel_validation)
            assert result.exists()  # Cancellation does not destroy a caller-owned archive.
        finally:
            result.unlink(missing_ok=True)


def test_archive_survives_source_database_and_storage_removal(tmp_path):
    """Only ZIP bytes cross the boundary; no source rows or storage remain reachable."""

    @contextmanager
    def installation(name):
        with pytest.MonkeyPatch.context() as patch:
            fixture = studio.__wrapped__(tmp_path / name, patch)
            try:
                yield next(fixture)
            finally:
                fixture.close()

    with installation("source") as source:
        client, _, _, path, tid, url = ready(source)
        change = draft(client, path, tid, url)
        assert preview(client, path, change)["preview"]["status"] == "succeeded"
        accept(client, path, change)
        response = client.get(path + "/archive")
        assert response.status_code == 200, response.text
        raw = response.content
    # The source schema is dropped by fixture cleanup. Rename the storage so even
    # accidental direct filesystem references cannot work during restoration.
    source_storage = tmp_path / "source" / "storage"
    source_storage.rename(tmp_path / "unavailable-source-storage")
    with installation("destination") as destination:
        client, _, ids = destination
        root = f"/api/v1/projects/{ids['project']}"
        work = post_ok(client, root + "/works", {"title": "独立环境恢复"})
        target = root + f"/works/{work['id']}/engineering"
        response = client.post(
            target + "/archive",
            data={"request_key": str(uuid.uuid4())},
            files={"file": ("work.zip", raw, "application/zip")},
        )
        assert response.status_code == 200, response.text
        accept(client, target, response.json())
        timeline = client.get(root + f"/works/{work['id']}/timelines").json()[0]
        url = root + f"/timelines/{timeline['id']}/document"
        candidate = draft(client, target, timeline["id"], url, revision=1)
        rendered = preview(client, target, candidate)
        assert rendered["preview"]["status"] == "succeeded", rendered
        assert rendered["preview"]["blob_hash"]


def test_archive_contains_verified_media_and_rejects_tampering(studio, tmp_path):
    client, _, _, path, tid, url = ready(studio)
    change = draft(client, path, tid, url)
    assert preview(client, path, change)["preview"]["status"] == "succeeded"
    accept(client, path, change)
    response = client.get(path + "/archive")
    assert response.status_code == 200
    file = tmp_path / "work.zip"
    file.write_bytes(response.content)
    envelope = archives.inspect(file)
    assert envelope["media"]
    assert set(envelope["package"]["external_media"]) == {m["hash"] for m in envelope["media"]}
    with zipfile.ZipFile(io.BytesIO(response.content)) as original:
        entries = {name: original.read(name) for name in original.namelist()}
    media_name = next(name for name in entries if name.startswith("media/"))
    raw = entries[media_name]
    entries[media_name] = bytes([raw[0] ^ 1]) + raw[1:]
    with zipfile.ZipFile(file, "w") as corrupt:
        for name, data in entries.items():
            corrupt.writestr(name, data)
    with pytest.raises(CapabilityUnsupported):
        archives.inspect(file)
    entries[media_name] = raw
    with zipfile.ZipFile(file, "w") as unknown:
        for name, data in entries.items():
            unknown.writestr(name, data)
        unknown.writestr("../outside.txt", "must not extract")
    with pytest.raises(CapabilityUnsupported):
        archives.inspect(file)
    assert not (tmp_path.parent / "outside.txt").exists()


def test_archive_restores_to_other_project_and_renders(studio):
    from app.models.identity import Membership

    client, sessions, ids, path, tid, url = ready(studio)
    change = draft(client, path, tid, url)
    assert preview(client, path, change)["preview"]["status"] == "succeeded"
    accept(client, path, change)
    raw = client.get(path + "/archive").content
    with sessions() as db:
        db.add(Membership(project_id=ids["other"], user_id=ids["user"], role="admin"))
        db.commit()
    root = f"/api/v1/projects/{ids['other']}"
    work = post_ok(client, root + "/works", {"title": "跨项目恢复"})
    target = root + f"/works/{work['id']}/engineering"
    key = str(uuid.uuid4())

    def upload():
        response = client.post(
            target + "/archive",
            data={"request_key": key},
            files={"file": ("work.zip", raw, "application/zip")},
        )
        assert response.status_code == 200, response.text
        return response.json()

    restored = upload()
    assert upload()["id"] == restored["id"]
    accept(client, target, restored)
    timeline = client.get(root + f"/works/{work['id']}/timelines").json()[0]
    new_url = root + f"/timelines/{timeline['id']}/document"
    doc = client.get(new_url).json()
    original = client.get(url).json()
    assert doc["items"][0]["shot_id"] is None
    assert doc["items"][0]["generation_id"] != original["items"][0]["generation_id"]
    edited = post_ok(
        client,
        target + "/changes",
        {
            "request_key": str(uuid.uuid4()),
            "base_revision": 1,
            "commands": [
                {"operation": "timeline.edit", "timeline_id": timeline["id"], "document": doc}
            ],
        },
    )
    assert preview(client, target, edited)["preview"]["status"] == "succeeded"
