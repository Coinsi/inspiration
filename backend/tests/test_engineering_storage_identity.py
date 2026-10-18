"""Immutable physical object identities make delayed deletion receipts safe."""

# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.models.storage import Blob
from app.storage import cas
from tests.test_media_workflow import studio  # noqa: F401


def test_repair_never_reuses_physical_identity_and_normal_dedupe_keeps_it(studio, tmp_path):
    _, sessions, _ = studio
    data = b"immutable source bytes"
    with sessions() as db:
        first = cas.put_bytes(db, data, "application/octet-stream")
        digest, original = first.hash, first.storage_uri
        db.commit()
    with sessions() as db:
        assert cas.put_bytes(db, data, "application/octet-stream").storage_uri == original
        db.commit()
    source = tmp_path / "repair.bin"
    source.write_bytes(data)
    with sessions() as db:
        repaired = cas.put_file(db, source, "application/octet-stream", force_store=True)
        replacement = repaired.storage_uri
        assert replacement != original and repaired.hash == digest
        db.commit()
    # Simulate a delayed cleanup targeting only its recorded old identity.
    Path(original).unlink()
    with sessions() as db:
        blob = db.get(Blob, digest)
        assert cas.read_bytes(blob) == data
        assert Path(replacement).is_file()
    Path(replacement).unlink()
    with sessions() as db:
        healed = cas.put_bytes(db, data, "application/octet-stream")
        assert healed.storage_uri not in (original, replacement)
        assert cas.read_bytes(healed) == data
        db.commit()


def test_concurrent_duplicate_writes_publish_one_object(studio):
    _, sessions, _ = studio

    def write(_):
        with sessions() as db:
            blob = cas.put_bytes(db, b"shared upload", "application/octet-stream")
            result = (blob.hash, blob.storage_uri)
            db.commit()
            return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(pool.map(write, range(4)))
    assert len(set(outputs)) == 1
    digest, uri = outputs[0]
    assert len(list(Path(uri).parent.glob(digest + ".*"))) == 1


def test_remote_repair_uses_a_distinct_object_key(studio, tmp_path, monkeypatch):
    import io

    from app.core.config import settings

    class Response(io.BytesIO):
        def release_conn(self):
            pass

    class Store:
        def __init__(self):
            self.objects = {}

        def put_object(self, bucket, key, source, length, **kwargs):
            self.objects[(bucket, key)] = source.read(length)

        def get_object(self, bucket, key, offset=0, length=0):
            data = self.objects[(bucket, key)]
            return Response(data[offset : offset + length] if length else data[offset:])

    _, sessions, _ = studio
    store = Store()
    monkeypatch.setattr(settings, "storage_backend", "minio")
    monkeypatch.setattr(cas, "_minio", lambda: store)
    source = tmp_path / "remote.bin"
    source.write_bytes(b"remote repair source")
    with sessions() as db:
        original = cas.put_file(db, source, "application/octet-stream")
        old_uri, digest = original.storage_uri, original.hash
        db.commit()
    with sessions() as db:
        new = cas.put_file(db, source, "application/octet-stream", force_store=True)
        assert new.storage_uri != old_uri
        db.commit()
        del store.objects[tuple(old_uri.split("/", 1))]
        assert cas.read_bytes(db.get(Blob, digest)) == source.read_bytes()
