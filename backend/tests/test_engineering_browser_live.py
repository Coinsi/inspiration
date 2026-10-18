"""Opt-in real UI -> API -> PostgreSQL -> CPU render acceptance in an isolated schema."""

# ruff: noqa: F811
import json
import os
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.engineering import EngineeringCommit
from app.models.generation import Generation
from tests.test_media_workflow import post_ok, studio, changing_video  # noqa: F401


def test_browser_creates_previews_adopts_and_reloads_real_engineering(
    studio, tmp_path, monkeypatch, changing_video
):
    if not os.environ.get("ENGINEERING_LIVE_BROWSER"):
        pytest.skip("Set ENGINEERING_LIVE_BROWSER for real frontend acceptance")
    client, sessions, ids = studio
    from app.modules.engineering import archive_jobs

    monkeypatch.setattr(archive_jobs, "SessionLocal", sessions)
    stop_archives = archive_jobs.start()
    root = f"/api/v1/projects/{ids['project']}"
    work = post_ok(client, root + "/works", {"title": "真实工程链路验收"})
    import_work = post_ok(client, root + "/works", {"title": "后台恢复验收"})

    from app.models.library import LibraryMedia, MediaVersion
    from app.modules.generation import media_engine
    from app.storage import cas
    with sessions() as db:
        blob = cas.put_bytes(db, media_engine.mock_image(), "image/png")
        media = LibraryMedia(project_id=uuid.UUID(ids["project"]), name="直接剪辑测试图片")
        db.add(media)
        db.flush()
        db.add(MediaVersion(media_id=media.id, ordinal=1, filename="edit.png", fingerprint="c" * 64,
            size_bytes=100, uploaded_bytes=100, status="ready", media_type="image",
            original_hash=blob.hash, poster_hash=blob.hash, width=320, height=180))
        import io, wave
        audio_data = io.BytesIO()
        with wave.open(audio_data, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\x00\x00" * 8000)
        sound = cas.put_bytes(db, audio_data.getvalue(), "audio/wav", duration_ms=1000)
        audio_media = LibraryMedia(project_id=uuid.UUID(ids["project"]), name="剪辑测试配乐")
        db.add(audio_media)
        db.flush()
        db.add(MediaVersion(media_id=audio_media.id, ordinal=1, filename="music.wav", fingerprint="d" * 64,
            size_bytes=len(audio_data.getvalue()), uploaded_bytes=len(audio_data.getvalue()), status="ready", media_type="audio",
            original_hash=sound.hash, duration_ms=1000))
        video = cas.put_bytes(db, changing_video, "video/mp4", duration_ms=2003)
        video_media = LibraryMedia(project_id=uuid.UUID(ids["project"]), name="剪辑测试视频")
        db.add(video_media)
        db.flush()
        db.add(MediaVersion(media_id=video_media.id, ordinal=1, filename="clip.mp4", fingerprint="e" * 64,
            size_bytes=100, uploaded_bytes=100, status="ready", media_type="video",
            original_hash=video.hash, duration_ms=2003, width=320, height=180))
        db.commit()

    class Bridge(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def handle_request(self):
            # Loopback bridge forwards to the actual application with isolated DB/storage.
            # No mocked route responses, no production credentials or user mutations.
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() in ("content-type", "range", "if-none-match")
            }
            response = client.request(self.command, self.path, content=body, headers=headers)
            self.send_response(response.status_code)
            for key, value in response.headers.items():
                if key.lower() not in ("content-length", "transfer-encoding", "content-encoding"):
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = handle_request

    class Server(ThreadingHTTPServer):
        request_queue_size = 128

    server = Server(("127.0.0.1", 0), Bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    environment = {
        **os.environ,
        "LIVE_API": f"http://127.0.0.1:{server.server_port}",
        "LIVE_TOKEN": client.headers["Authorization"].removeprefix("Bearer "),
        "LIVE_PROJECT": ids["project"],
        "LIVE_WORK": work["id"],
        "LIVE_IMPORT_WORK": import_work["id"],
        "ARTIFACT_DIR": str(frontend / "test-results" / "engineering-live"),
    }
    try:
        result = subprocess.run(
            ["node", "tests/engineering-live-browser.mjs"],
            cwd=frontend,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        data = client.get(root + f"/works/{work['id']}/engineering").json()
        assert data["revision"] == 1
        imported_jobs = client.get(
            root + f"/works/{import_work['id']}/engineering/archive-jobs"
        ).json()
        assert len(imported_jobs) == 1 and imported_jobs[0]["status"] == "succeeded"
        assert imported_jobs[0]["progress"]["change_id"]
        assert client.get(root + f"/works/{import_work['id']}/engineering").json()["revision"] == 0
        linked = client.get(root + f"/works/{work['id']}/timelines").json()
        assert len(linked) == 1
        assert client.get(root + f"/timelines/{linked[0]['id']}/document").status_code == 200
        with sessions() as db:
            commits = list(db.scalars(select(EngineeringCommit)))
            results = list(db.scalars(select(Generation)))
            assert len(commits) == 1 and len(results) == 6
            assert any(g.input_refs.get("engineering_plan", {}).get("executor") for g in results)
        # Inspect the file downloaded by the browser, not a second API export.
        from app.modules.engineering import packages

        exported = json.loads(
            (frontend / "test-results" / "engineering-live" / "export.json").read_text(
                encoding="utf-8"
            )
        )
        _, nodes = packages.inspect(exported)
        from app.modules.engineering import archives

        archived = archives.inspect(frontend / "test-results" / "engineering-live" / "export.zip")
        assert archived["package"]["format"] == exported["format"]
        image = next(node for node in nodes if node.kind == "image.document")
        base = root + f"/works/{work['id']}/engineering"
        post_ok(
            client,
            base + "/changes",
            {
                "request_key": str(uuid.uuid4()),
                "base_revision": 1,
                "commands": [
                    {
                        "operation": "document.put",
                        "document_id": str(image.id),
                        "expected_revision": image.revision,
                        "kind": image.kind,
                        "title": image.title,
                        "inputs": image.inputs,
                        "payload": {
                            **image.payload,
                            "canvas": {**image.payload["canvas"], "width": 640},
                        },
                    }
                ],
            },
        )
        old = client.get(base + f"/documents/{image.id}/revisions/1").json()
        new = client.get(base + f"/documents/{image.id}/revisions/2").json()
        assert len(old["artifacts"]) == 1 and new["artifacts"] == []
        other = post_ok(client, root + "/works", {"title": "隔离作品"})
        assert (
            client.get(
                root + f"/works/{other['id']}/engineering/documents/{image.id}/revisions/1"
            ).status_code
            == 404
        )
        fixed = data["snapshot"]["native_artifacts"]
        assert len(fixed) == 1
        rerender = post_ok(
            client,
            base + "/changes",
            {
                "request_key": str(uuid.uuid4()),
                "base_revision": 1,
                "commands": [{"operation": "restore", "revision": 1}],
            },
        )
        post_ok(
            client,
            base + f"/changes/{rerender['id']}/preview",
            {"fingerprint": rerender["fingerprint"]},
        )
        assert client.get(base).json()["snapshot"]["native_artifacts"] == fixed
        post_ok(
            client,
            base + f"/changes/{rerender['id']}/accept",
            {"fingerprint": rerender["fingerprint"]},
        )
        updated = client.get(base).json()["snapshot"]["native_artifacts"]
        assert updated != fixed
        history = client.get(base + "/history").json()
        assert history[1]["snapshot"]["native_artifacts"] == fixed
        (frontend / "test-results" / "engineering-live" / "database.json").write_text(
            json.dumps(
                {
                    "initial_commits": 1,
                    "initial_renders": 1,
                    "final_revision": 2,
                    "frozen_history_verified": True,
                }
            ),
            encoding="utf-8",
        )
    finally:
        stop_archives()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
