"""Real isolated-DB attachment lifecycle without index workers or model requests."""

# ruff: noqa: F811

import hashlib
import importlib.util
import io
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from PIL import Image
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.deps import ProjectContext
from app.core.errors import Conflict, NotFound
from app.core.security import create_access_token
from app.models.attachment import Attachment
from app.models.conversation import Conversation, ConversationMessage
from app.models.identity import Membership, Project, User
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_index import MediaIndex
from app.models.storage import Blob
from app.modules.attachments import cleanup, schemas, service
from app.modules.generation import media_engine
from app.storage import retention
from tests.test_media_workflow import post_ok, studio  # noqa: F401


@pytest.fixture
def attachments(studio, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "library_staging_dir", str(tmp_path / "staging"))
    monkeypatch.setattr(settings, "library_staging_reserve_bytes", 0)
    monkeypatch.setattr(service, "CHUNK_SIZE", 64)
    return studio


def picture():
    output = io.BytesIO()
    Image.new("RGB", (40, 24), (120, 80, 40)).save(output, "PNG")
    return output.getvalue()


def base(ids):
    return f"/api/v1/projects/{ids['project']}/attachments"


def start(client, ids, data, filename="参考.png", request_key=None):
    return post_ok(
        client,
        base(ids) + "/uploads",
        {
            "request_key": str(request_key or uuid.uuid4()),
            "filename": filename,
            "size_bytes": len(data),
            "fingerprint": hashlib.sha256(data).hexdigest(),
        },
    )


def chunks(client, ids, row, data):
    for offset in range(row["uploaded_bytes"], len(data), row["chunk_size"]):
        result = client.post(
            f"{base(ids)}/{row['id']}/chunk?offset={offset}",
            files={"file": ("chunk", data[offset : offset + row["chunk_size"]])},
        )
        assert result.status_code == 200, result.text
        row = result.json()
    return row


def upload(client, ids, data=None, filename="参考.png"):
    data = picture() if data is None else data
    row = chunks(client, ids, start(client, ids, data, filename), data)
    return post_ok(client, f"{base(ids)}/{row['id']}/complete")


def context(db, ids):
    project = db.get(Project, uuid.UUID(ids["project"]))
    user = db.get(User, ids["user"])
    membership = db.scalar(
        select(Membership).where(
            Membership.project_id == project.id,
            Membership.user_id == user.id,
        )
    )
    return ProjectContext(project, membership, user)


def save(db, ids, attachment_ids):
    ctx = context(db, ids)
    conversation = Conversation(project_id=ctx.project.id, created_by=ctx.user.id)
    db.add(conversation)
    db.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        ordinal=1,
        role="user",
        request_key=uuid.uuid4(),
        content="帮我看看这张图",
    )
    db.add(message)
    db.flush()
    result = service.bind(db, ctx, attachment_ids, conversation.id, message.id)
    return result, conversation.id


def test_upload_ready_without_index_and_cas_dedup(attachments):
    client, sessions, ids = attachments
    data = picture()
    first = upload(client, ids, data)
    assert first["status"] == "ready" and first["saved_at"] is None
    assert first["title"] == "参考" and first["filename"] == "参考.png"
    assert (first["width"], first["height"]) == (40, 24)
    assert first["blob_hash"] == hashlib.sha256(data).hexdigest()
    assert first["thumbnail_hash"] == first["blob_hash"]
    assert client.get(base(ids)).json() == {"items": [], "has_more": False}
    response = client.get(f"/api/v1/projects/{ids['project']}/blobs/{first['blob_hash']}")
    assert response.status_code == 200 and response.content == data
    assert not service.staging_path(first["id"]).exists()
    second = upload(client, ids, data)
    assert second["id"] != first["id"] and second["blob_hash"] == first["blob_hash"]
    assert post_ok(client, f"{base(ids)}/{first['id']}/complete")["blob_hash"] == first["blob_hash"]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Blob)) == 1
        assert db.scalar(select(func.count()).select_from(LibraryMedia)) == 0
        assert db.scalar(select(func.count()).select_from(MediaIndex)) == 0
        assert retention.inspect_blob(db, first["blob_hash"])["retained"]


def test_resume_manifest_and_idempotent_admission(attachments):
    client, _, ids = attachments
    data, key = picture(), uuid.uuid4()
    row = start(client, ids, data, request_key=key)
    assert start(client, ids, data, request_key=key)["id"] == row["id"]
    bad = client.post(
        base(ids) + "/uploads",
        json={
            "request_key": str(key),
            "filename": "不同.png",
            "size_bytes": len(data),
            "fingerprint": hashlib.sha256(data).hexdigest(),
        },
    )
    assert bad.status_code == 409
    url = f"{base(ids)}/{row['id']}"
    first = client.post(url + "/chunk?offset=0", files={"file": ("chunk", data[:64])})
    assert first.status_code == 200
    manifest = client.get(url + "/manifest").json()
    assert manifest["uploaded_bytes"] == 64
    assert manifest["chunk_hashes"] == [hashlib.sha256(data[:64]).hexdigest()]
    assert manifest["fingerprint"] == hashlib.sha256(data).hexdigest()
    assert (
        client.post(url + "/chunk?offset=0", files={"file": ("chunk", data[:64])}).status_code
        == 409
    )
    assert client.post(url + "/complete").status_code == 409
    chunks(client, ids, manifest, data)
    assert post_ok(client, url + "/complete")["status"] == "ready"


def test_wrong_content_is_failed_not_saved_or_indexed(attachments):
    client, _, ids = attachments
    row = upload(client, ids, b"not an image", "photo.png")
    assert row["status"] == "failed" and row["blob_hash"] is None
    assert row["error"] and "staging" not in row["error"]
    assert client.get(f"{base(ids)}/{row['id']}").json()["status"] == "failed"
    assert post_ok(client, f"{base(ids)}/{row['id']}/complete")["status"] == "failed"


def test_send_saves_and_reuse_preserves_origin(attachments):
    client, sessions, ids = attachments
    row = upload(client, ids)
    with sessions() as db:
        saved, original = save(db, ids, [row["id"]])
        db.commit()
        assert saved[0]["saved_at"]
        again, _ = save(db, ids, [row["id"]])
        assert again[0]["conversation_id"] == str(original)
        db.commit()
    catalog = client.get(base(ids) + "?query=参考&kind=image").json()
    assert client.get(f"{base(ids)}/{row['id']}").json()["message_ordinal"] == 1
    assert catalog["items"][0]["id"] == row["id"] and not catalog["has_more"]
    assert client.get(base(ids) + "?kind=audio").json()["items"] == []
    assert client.delete(f"{base(ids)}/{row['id']}").status_code == 409
    # Message removal does not silently delete an explicitly retained attachment.
    with sessions() as db:
        db.delete(db.get(Conversation, original))
        db.commit()
    assert client.get(base(ids)).json()["items"][0]["saved_at"]
    assert client.get(f"{base(ids)}/{row['id']}").json()["message_ordinal"] is None


def test_send_failure_rolls_back_adoption_and_unready_cannot_bind(attachments):
    client, sessions, ids = attachments
    row = upload(client, ids)
    pending = start(client, ids, picture())
    with sessions() as db:
        with pytest.raises(Conflict):
            save(db, ids, [row["id"], pending["id"]])
        db.rollback()
    assert client.get(base(ids)).json()["items"] == []
    assert client.delete(f"{base(ids)}/{row['id']}").status_code == 200


def test_metadata_revision_preserves_original_filename(attachments):
    client, _, ids = attachments
    row = upload(client, ids)
    url = f"{base(ids)}/{row['id']}"
    result = client.patch(
        url,
        json={
            "title": "女主参考",
            "description": "金色背景",
            "tags": ["角色", " 角色 "],
            "expected_revision": 1,
        },
    )
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["title"] == "女主参考" and value["filename"] == "参考.png"
    assert value["tags"] == ["角色"] and value["metadata_revision"] == 2
    assert client.patch(url, json={"title": "冲突", "expected_revision": 1}).status_code == 409
    assert client.patch(url, json={"title": "缺少版本"}).status_code == 422
    assert client.patch(url, json={"favorite": True}).json()["favorite"]


def test_draft_cancel_releases_only_own_reference(attachments):
    client, _, ids = attachments
    first, second = upload(client, ids), upload(client, ids)
    url = f"{base(ids)}/{first['id']}"
    assert client.delete(url).json()["status"] == "cancelled"
    assert client.get(url).json()["blob_hash"] is None
    assert client.delete(url).status_code == 200
    assert client.post(url + "/complete").status_code == 409
    assert (
        client.get(f"/api/v1/projects/{ids['project']}/blobs/{second['blob_hash']}").status_code
        == 200
    )


def test_private_owner_and_project_boundaries(attachments):
    client, sessions, ids = attachments
    row = upload(client, ids)
    with sessions() as db:
        other = User(
            username="attachment_other",
            email="attachment_other@example.com",
            password_hash="unused",
            display_name="Other",
        )
        db.add(other)
        db.flush()
        db.add(Membership(project_id=uuid.UUID(ids["project"]), user_id=other.id, role="admin"))
        db.commit()
        other_id = other.id
    client.headers["Authorization"] = f"Bearer {create_access_token(str(other_id))}"
    url = f"{base(ids)}/{row['id']}"
    assert client.get(url).status_code == 404
    assert client.get(url + "/manifest").status_code == 404
    assert client.delete(url).status_code == 404
    assert (
        client.get(f"/api/v1/projects/{ids['project']}/blobs/{row['blob_hash']}").status_code == 404
    )
    with sessions() as db:
        ctx = context(db, ids)
        ctx.user = db.get(User, other_id)
        with pytest.raises(NotFound):
            service.resolve(db, ctx, row["id"])
    client.headers["Authorization"] = f"Bearer {create_access_token(str(ids['user']))}"
    assert client.get(f"/api/v1/projects/{ids['other']}/attachments/{row['id']}").status_code == 403


def test_concurrent_request_key_only_reserves_once(attachments):
    _, sessions, ids = attachments
    value = schemas.UploadIn(
        request_key=uuid.uuid4(), filename="并发.png", size_bytes=100, fingerprint="a" * 64
    )

    def begin():
        with sessions() as db:
            result = service.begin(db, context(db, ids), value)
            db.commit()
            return result["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: begin(), range(2)))
    assert results[0] == results[1]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Attachment)) == 1


def test_shared_library_budget_and_cancel_release(attachments, monkeypatch):
    client, sessions, ids = attachments
    monkeypatch.setattr(settings, "library_staging_max_bytes", 300)
    with sessions() as db:
        media = LibraryMedia(project_id=uuid.UUID(ids["project"]), name="Existing")
        db.add(media)
        db.flush()
        db.add(
            MediaVersion(
                media_id=media.id,
                ordinal=1,
                filename="old.mp4",
                media_type="video",
                fingerprint="b" * 64,
                size_bytes=200,
                uploaded_bytes=0,
                status="uploading",
            )
        )
        db.commit()
    row = start(client, ids, b"x" * 100)
    rejected = client.post(
        base(ids) + "/uploads",
        json={
            "request_key": str(uuid.uuid4()),
            "filename": "more.png",
            "size_bytes": 1,
            "fingerprint": "c" * 64,
        },
    )
    assert rejected.status_code == 409
    assert client.delete(f"{base(ids)}/{row['id']}").status_code == 200
    assert start(client, ids, b"x" * 100)["status"] == "uploading"


def test_audio_video_probe_and_range_read(attachments, tmp_path, monkeypatch):
    client, _, ids = attachments
    monkeypatch.setattr(service, "CHUNK_SIZE", 8 * 1024 * 1024)
    wav = io.BytesIO()
    with wave.open(wav, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\x00\x00" * 8000)
    audio = upload(client, ids, wav.getvalue(), "配音.wav")
    assert audio["status"] == "ready", audio
    assert audio["kind"] == "audio" and audio["has_audio"] and audio["duration_ms"] == 1000
    path = tmp_path / "sample.mp4"
    media_engine.run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x96:r=24:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    video = upload(client, ids, path.read_bytes(), "镜头.mp4")
    assert video["status"] == "ready", video
    assert video["width"] == 160 and video["duration_ms"] == 1000
    response = client.get(
        f"/api/v1/projects/{ids['project']}/blobs/{video['blob_hash']}",
        headers={"Range": "bytes=0-15"},
    )
    assert response.status_code == 206 and response.content == path.read_bytes()[:16]


def test_schema_validation_limits(attachments, monkeypatch):
    client, _, ids = attachments
    monkeypatch.setattr(settings, "library_max_upload_bytes", 100)
    response = client.post(
        base(ids) + "/uploads",
        json={
            "request_key": str(uuid.uuid4()),
            "filename": "too-big.png",
            "size_bytes": 101,
            "fingerprint": "e" * 64,
        },
    )
    assert response.status_code == 422
    value = schemas.UploadIn(
        request_key=uuid.uuid4(), filename="C:\\tmp\\image.png", size_bytes=10, fingerprint="e" * 64
    )
    assert value.filename == "image.png"
    response = client.post(
        base(ids) + "/uploads",
        json={
            "request_key": str(uuid.uuid4()),
            "filename": "unsafe.svg",
            "size_bytes": 10,
            "fingerprint": "e" * 64,
        },
    )
    assert response.status_code == 422


def test_hide_and_restore_do_not_break_saved_conversation_references(attachments):
    client, sessions, ids = attachments
    row = upload(client, ids)
    with sessions() as db:
        with pytest.raises(Conflict):
            service.set_hidden(db, context(db, ids), row["id"], True)
        db.rollback()
        save(db, ids, [row["id"]])
        db.commit()
        assert service.set_hidden(db, context(db, ids), row["id"], True) == {
            "restored": False,
            "physical_delete": False,
        }
        db.commit()
        assert service.resolve(db, context(db, ids), row["id"])["blob_hash"] == row["blob_hash"]
    assert client.get(base(ids)).json()["items"] == []
    assert (
        client.get(f"/api/v1/projects/{ids['project']}/blobs/{row['blob_hash']}").status_code == 200
    )
    with sessions() as db:
        assert service.set_hidden(db, context(db, ids), row["id"], False)["restored"]
        db.commit()
    assert client.get(base(ids)).json()["items"][0]["id"] == row["id"]


def test_metadata_concurrent_edits_do_not_overwrite(attachments):
    client, sessions, ids = attachments
    row = upload(client, ids)

    def change(title):
        with sessions() as db:
            try:
                service.update(
                    db,
                    context(db, ids),
                    row["id"],
                    schemas.UpdateIn(title=title, expected_revision=1),
                )
                db.commit()
                return "saved"
            except Conflict:
                db.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change, ["甲", "乙"])) == ["conflict", "saved"]


def test_completed_source_retained_until_transaction_commits(attachments):
    client, sessions, ids = attachments
    data = picture()
    row = chunks(client, ids, start(client, ids, data), data)
    with sessions() as db:
        assert service.complete(db, context(db, ids), row["id"])["status"] == "ready"
        db.rollback()
    # A DB failure after storage publication must not discard resumable upload bytes.
    assert service.staging_path(row["id"]).read_bytes() == data
    assert client.get(f"{base(ids)}/{row['id']}").json()["status"] == "uploading"
    assert post_ok(client, f"{base(ids)}/{row['id']}/complete")["status"] == "ready"


def test_attachment_migration_upgrade_and_downgrade(attachments):
    client, sessions, ids = attachments
    path = Path(__file__).parents[1] / "alembic/versions/0055_conversation_attachments.py"
    spec = importlib.util.spec_from_file_location("attachment_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sessions() as db:
        connection = db.connection()
        Attachment.__table__.drop(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.downgrade()
            migration.upgrade()
        db.commit()
    assert upload(client, ids)["status"] == "ready"


def test_expire_only_inactive_unsent_drafts_and_release_budget(attachments, monkeypatch):
    client, sessions, ids = attachments
    monkeypatch.setattr(settings, "library_staging_max_bytes", 100)
    row = start(client, ids, b"x" * 100)
    old = datetime.now(UTC) - timedelta(days=31)
    with sessions() as db:
        db.execute(update(Attachment).where(Attachment.id == row["id"]).values(updated_at=old))
        db.commit()
    assert cleanup.run_once(sessions) == 1
    result = client.get(f"{base(ids)}/{row['id']}").json()
    assert result["status"] == "cancelled" and "过期" in result["error"]
    assert start(client, ids, b"y" * 100)["status"] == "uploading"


def test_expiry_preserves_saved_hidden_and_active_uploads(attachments):
    client, sessions, ids = attachments
    ready = upload(client, ids)
    saved = upload(client, ids)
    active_data = picture()
    active = start(client, ids, active_data)
    old = datetime.now(UTC) - timedelta(days=31)
    with sessions() as db:
        save(db, ids, [saved["id"]])
        service.set_hidden(db, context(db, ids), saved["id"], True)
        db.execute(update(Attachment).values(updated_at=old))
        db.commit()
    # A newly committed chunk renews the inactive-upload lease.
    assert (
        client.post(
            f"{base(ids)}/{active['id']}/chunk?offset=0",
            files={"file": ("chunk", active_data[:64])},
        ).status_code
        == 200
    )
    assert cleanup.run_once(sessions) == 1
    assert client.get(f"{base(ids)}/{ready['id']}").json()["status"] == "cancelled"
    assert client.get(f"{base(ids)}/{active['id']}").json()["status"] == "uploading"
    with sessions() as db:
        assert service.resolve(db, context(db, ids), saved["id"])["blob_hash"] == saved["blob_hash"]
        assert db.get(Blob, ready["blob_hash"]) is not None


def test_expiry_skips_rows_currently_being_modified(attachments):
    client, sessions, ids = attachments
    row = start(client, ids, picture())
    with sessions() as db:
        db.execute(update(Attachment).values(updated_at=datetime.now(UTC) - timedelta(days=31)))
        db.commit()
    with sessions() as active:
        service.get(active, context(active, ids), row["id"], lock=True)
        assert cleanup.run_once(sessions) == 0
        active.rollback()
    assert cleanup.run_once(sessions) == 1


def test_cleanup_recovers_committed_source_without_expiring_saved_file(attachments):
    client, sessions, ids = attachments
    data = picture()
    row = chunks(client, ids, start(client, ids, data), data)
    with sessions() as db:
        service.complete(db, context(db, ids), row["id"])
        save(db, ids, [row["id"]])
        db.commit()
    assert service.staging_path(row["id"]).exists()
    assert cleanup.run_once(sessions) == 0
    assert not service.staging_path(row["id"]).exists()
    assert client.get(f"{base(ids)}/{row['id']}").json()["status"] == "ready"
