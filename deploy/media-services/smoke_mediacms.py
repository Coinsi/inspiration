"""Real MediaCMS smoke test. Use only a small synthetic fixture, not private video.

Checks upload idempotency, real encoding outputs, and unauthenticated rejection.
This does not measure semantic retrieval, speech recognition, or long-video scale.
"""
import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).with_name(".env"))
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.path:
        raise SystemExit("Smoke test only accepts the local integration endpoint")
    if not 0 < args.source.stat().st_size < 32 * 1024 * 1024:
        raise SystemExit("Use a synthetic fixture smaller than 32 MiB")
    env = {}
    for line in args.env_file.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    token = env["MEDIACMS_SERVICE_TOKEN"]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(path, *, body=None, content_type=None, authorized=True, method=None):
        if not path.startswith("/") or path.startswith("//"):
            raise RuntimeError("Unexpected external output path")
        headers = {"Authorization": "Token " + token} if authorized else {}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
        try:
            with opener.open(req, timeout=60) as response:
                return response.status, response.read(40 * 1024 * 1024)
        except urllib.error.HTTPError as error:
            return error.code, error.read(8192)

    source = args.source.read_bytes()
    upload_key = "smoke-" + uuid.uuid4().hex
    boundary = "inspiration-smoke-" + uuid.uuid4().hex
    body = bytearray()
    fields = {
        "upload_key": upload_key,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "title": "Inspiration synthetic integration smoke",
    }
    for key, value in fields.items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="media_file"; filename="smoke.mp4"\r\nContent-Type: video/mp4\r\n\r\n'.encode())
    body.extend(source)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    content_type = "multipart/form-data; boundary=" + boundary
    remote_id = None
    completed = False
    try:
        status, data = request("/api/inspiration/v1/media/", body=bytes(body), content_type=content_type)
        if status != 201:
            raise RuntimeError(f"Real bridge upload failed with HTTP {status}")
        remote_id = json.loads(data)["friendly_token"]
        print(json.dumps({"stage": "uploaded", "remote_id": remote_id}), flush=True)
        status, data = request("/api/inspiration/v1/media/", body=bytes(body), content_type=content_type)
        if status != 200 or json.loads(data)["friendly_token"] != remote_id:
            raise RuntimeError("Identical upload did not return the same upstream record")
        manifest_path = f"/api/inspiration/v1/media/{remote_id}/manifest"
        status, _ = request(manifest_path, authorized=False)
        if status not in {401, 403}:
            raise RuntimeError("Unauthenticated manifest access was not rejected")
        deadline = time.monotonic() + args.timeout
        last_state = None
        while time.monotonic() < deadline:
            status, data = request(manifest_path)
            if status != 200:
                raise RuntimeError(f"Manifest failed with HTTP {status}")
            manifest = json.loads(data)
            # Upstream encoding_status means at least one rendition is usable.
            # Our completion barrier waits for every snapshotted profile.
            progress = manifest.get("processing")
            if not isinstance(progress, dict):
                raise RuntimeError("Bridge does not expose profile completion state")
            state = progress["status"]
            if state != last_state:
                print(json.dumps({"stage": "encoding", "state": state}), flush=True)
                last_state = state
            if (
                manifest.get("encodings")
                and manifest.get("thumbnail")
                and manifest.get("preview")
                and state == "success"
                and progress.get("complete") is True
                and progress["expected"] == progress["terminal"]
                and progress["expected"] > 0
                and not progress["pending"]
                and not progress["running"]
                and not progress["failed"]
            ):
                output = manifest["encodings"][0]
                status, payload = request(output["url"])
                if status != 200 or len(payload) != output["size_bytes"] or not payload:
                    raise RuntimeError("Authenticated encoded output download failed")
                status, _ = request(output["url"], authorized=False)
                if status not in {401, 403}:
                    raise RuntimeError("Unauthenticated encoded output access was not rejected")
                completed = True
                print(json.dumps({"result": "passed", "remote_id": remote_id, "encoding_count": len(manifest["encodings"]), "completed_profiles": progress["terminal"], "download_bytes": len(payload), "idempotency": "passed", "private_download": "passed"}), flush=True)
                return
            if state == "fail":
                raise RuntimeError("MediaCMS reported a real encoding failure")
            time.sleep(3)
        raise RuntimeError("Real encoding did not complete before the bounded smoke timeout")
    finally:
        if remote_id and completed and not args.keep:
            status, _ = request(f"/api/v1/media/{remote_id}", method="DELETE")
            print(json.dumps({"cleanup_http_status": status}), flush=True)
        elif remote_id:
            print(json.dumps({"retained_remote_id": remote_id}), flush=True)


if __name__ == "__main__":
    main()
