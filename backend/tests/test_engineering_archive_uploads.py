"""Resumable upload receipts and background import use real database transactions."""

# ruff: noqa: F811
import uuid

from app.modules.engineering import archive_jobs
from tests.test_engineering import accept
from tests.test_engineering_review import draft, preview, ready
from tests.test_media_workflow import post_ok, studio  # noqa: F401


def test_upload_retransmission_crash_tail_and_background_import(studio):
    client, sessions, ids, path, tid, url = ready(studio)
    change = draft(client, path, tid, url)
    assert preview(client, path, change)["preview"]["status"] == "succeeded"
    accept(client, path, change)
    data = client.get(path + "/archive").content
    root = f"/api/v1/projects/{ids['project']}"
    work = post_ok(client, root + "/works", {"title": "恢复工程"})
    dest = root + f"/works/{work['id']}/engineering"
    key = uuid.uuid4()
    creation = dest + f"/archive-uploads?request_key={key}&size={len(data)}&filename=work.zip"
    job = post_ok(client, creation, {})
    assert post_ok(client, creation, {})["id"] == job["id"]
    jid = uuid.UUID(job["id"])
    upload = dest + f"/archive-uploads/{jid}"
    cut = len(data) // 2
    assert client.put(upload + "?offset=0", content=data[:cut]).status_code == 200
    assert client.put(upload + "?offset=0", content=data[:cut]).status_code == 200
    assert client.put(upload + "?offset=0", content=b"wrong").status_code == 409
    assert client.post(upload + "/complete").status_code == 409
    assert client.put(upload + f"?offset={cut + 1}", content=b"x").status_code == 409
    # Bytes may reach disk without a committed receipt. A new attempt must replace that tail.
    with (archive_jobs.directory(jid) / "upload.zip").open("ab") as stream:
        stream.write(b"uncommitted tail")
    receipt = client.put(upload + f"?offset={cut}", content=data[cut:])
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["uploaded_bytes"] == len(data)
    assert post_ok(client, upload + "/complete", {})["status"] == "pending"
    assert post_ok(client, upload + "/complete", {})["status"] == "pending"
    assert archive_jobs.execute(jid, sessions=sessions)
    completed = client.get(dest + "/archive-jobs").json()[0]
    assert completed["status"] == "succeeded"
    assert completed["progress"]["change_id"]
    assert client.get(dest).json()["revision"] == 0  # Restore is still a review candidate.
    assert not archive_jobs.execute(jid, sessions=sessions)
    assert client.get(path + f"/archive-jobs/{jid}/download").status_code == 404


def test_partial_upload_cancellation_resumes_without_queueing_incomplete_data(studio):
    client, sessions, _, path, _, _ = ready(studio)
    job = post_ok(
        client, path + f"/archive-uploads?request_key={uuid.uuid4()}&size=6&filename=x.zip", {}
    )
    jid = job["id"]
    upload = path + f"/archive-uploads/{jid}"
    assert client.put(upload + "?offset=0", content=b"abc").status_code == 200
    post_ok(client, path + f"/archive-jobs/{jid}/cancel", {})
    assert client.put(upload + "?offset=3", content=b"def").status_code == 409
    resumed = post_ok(client, path + f"/archive-jobs/{jid}/retry", {})
    assert resumed["status"] == "uploading" and resumed["uploaded_bytes"] == 3
    assert client.put(upload + "?offset=3", content=b"def").status_code == 200
    post_ok(client, upload + "/complete", {})
    assert not archive_jobs.execute(uuid.UUID(jid), sessions=sessions)
    assert client.get(path + "/archive-jobs").json()[0]["status"] == "failed"
    assert client.get(path).json()["revision"] == 0
