"""A failed copy must never replace the last verified storage address."""

import io
import json
from pathlib import Path

import pytest

from app import maintenance
from app.core.config import settings
from app.models.storage import Blob
from app.storage import cas
from tests.test_media_workflow import studio  # noqa: F401


class Response(io.BytesIO):
    def release_conn(self):
        pass


class ObjectStore:
    def __init__(self, corrupt=False):
        self.objects = {}
        self.corrupt = corrupt

    def put_object(self, bucket, name, stream, length, **kwargs):
        self.objects[bucket, name] = stream.read(length)

    def get_object(self, bucket, name):
        return Response(b"corrupt" if self.corrupt else self.objects[bucket, name])


@pytest.mark.parametrize("failure", [None, "missing", "source_corrupt", "target_corrupt"])
def test_migration_verifies_both_sides_before_committing(studio, monkeypatch, failure):
    _, sessions, _ = studio
    monkeypatch.setattr(maintenance, "SessionLocal", sessions)
    with sessions() as db:
        blob = cas.put_bytes(db, b"unique-storage-migration-fixture", "application/octet-stream")
        key, old_uri = blob.hash, blob.storage_uri
        db.commit()
    source = Path(old_uri)
    if failure == "missing":
        source.unlink()
    elif failure == "source_corrupt":
        source.write_bytes(b"wrong source")
    store = ObjectStore(corrupt=failure == "target_corrupt")
    monkeypatch.setattr(cas, "_minio", lambda: store)
    monkeypatch.setattr(settings, "storage_backend", "minio")
    if failure:
        with pytest.raises(ValueError):
            maintenance.migrate_fs_to_minio()
        with sessions() as db:
            assert db.get(Blob, key).storage_uri == old_uri
        if failure != "target_corrupt":
            assert not store.objects
    else:
        assert maintenance.migrate_fs_to_minio()["migrated"] == 1
        with sessions() as db:
            uri = db.get(Blob, key).storage_uri
            assert not Path(uri).is_absolute()
            assert store.objects[tuple(uri.split("/", 1))] == source.read_bytes()
        assert source.is_file()
        assert maintenance.migrate_fs_to_minio()["migrated"] == 0


def test_empty_inventory_is_json_serializable(studio, monkeypatch):
    monkeypatch.setattr(maintenance, "SessionLocal", studio[1])
    assert json.loads(json.dumps(maintenance.inventory()))["stored_bytes"] == 0
