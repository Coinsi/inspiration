"""Small, digest-pinned release bundles. Registry credentials never enter releases."""

import base64
import hashlib
import io
import json
import os
import re
import sys
import tarfile
from pathlib import Path

FILES = {"compose.yml", "nginx.conf", "deploy.sh", "release.json", "registry-auth.json"}
LEGACY_FILES = {"compose.yml", "nginx.conf", "deploy.sh", "images.tar.gz"}
IMAGE_ROOT = "ghcr.io/coinsi/inspiration"


def validate(manifest, release):
    if not re.fullmatch(r"[0-9a-f]{40}", release):
        raise ValueError("Invalid commit")
    if manifest.get("version") != 2 or manifest.get("release") != release:
        raise ValueError("Release manifest does not match requested commit")
    for role in ("api", "web"):
        if not re.fullmatch(
            re.escape(f"{IMAGE_ROOT}-{role}@sha256:") + r"[0-9a-f]{64}",
            manifest.get("images", {}).get(role, ""),
        ):
            raise ValueError("Invalid image repository or digest")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest.get("schema", "")):
        raise ValueError("Invalid schema fingerprint")


def package():
    root = Path(__file__).resolve().parent
    schema = hashlib.sha256()
    backend = root.parent.parent / "backend"
    for path in sorted((backend / "alembic").rglob("*.py")) + [backend / "alembic.ini"]:
        schema.update(
            path.relative_to(backend).as_posix().encode() + b"\0" + path.read_bytes()
        )
    manifest = {
        "version": 2,
        "release": os.environ["RELEASE"],
        "schema": schema.hexdigest(),
        "images": {
            role: f"{os.environ['IMAGE_ROOT']}-{role}@{os.environ[role.upper() + '_DIGEST']}"
            for role in ("api", "web")
        },
    }
    validate(manifest, manifest["release"])
    data = {
        name: (root / name).read_bytes()
        for name in ("compose.yml", "nginx.conf", "deploy.sh")
    }
    data["release.json"] = json.dumps(manifest).encode()
    data["registry-auth.json"] = json.dumps(
        {
            "username": os.environ["REGISTRY_USER"],
            "password": os.environ["REGISTRY_TOKEN"],
        }
    ).encode()
    with tarfile.open("release.tgz", "w:gz") as archive:
        for name, content in data.items():
            entry = tarfile.TarInfo(name)
            entry.mode, entry.size = 0o600, len(content)
            archive.addfile(entry, io.BytesIO(content))
    os.chmod("release.tgz", 0o600)


def unpack(bundle, destination, release):
    destination = Path(destination)
    with tarfile.open(bundle, "r:gz") as archive:
        members = archive.getmembers()
        names = {m.name for m in members}
        if (
            names not in (FILES, LEGACY_FILES)
            or len(names) != len(members)
            or not all(m.isfile() for m in members)
        ):
            raise ValueError("Invalid release archive")
        if names == FILES and sum(m.size for m in members) > 2 * 1024 * 1024:
            raise ValueError("Manifest bundle too large")
        if names == FILES:
            manifest = json.load(archive.extractfile("release.json"))
            validate(manifest, release)
            auth = json.load(archive.extractfile("registry-auth.json"))
            if (
                not isinstance(auth.get("username"), str)
                or not isinstance(auth.get("password"), str)
                or not all(auth.values())
            ):
                raise ValueError("Missing temporary registry credentials")
            config_dir = destination / "docker-auth"
            config_dir.mkdir(mode=0o700)
            credential = base64.b64encode(
                f"{auth['username']}:{auth['password']}".encode()
            ).decode()
            config = config_dir / "config.json"
            config.write_text(json.dumps({"auths": {"ghcr.io": {"auth": credential}}}))
            config.chmod(0o600)
            env = destination / "release-images.env"
            env.write_text(
                f"API_IMAGE={manifest['images']['api']}\nWEB_IMAGE={manifest['images']['web']}\n"
            )
            env.chmod(0o600)
        for member in members:
            if member.name == "registry-auth.json":
                continue
            target = destination / member.name
            with archive.extractfile(member) as src, target.open("wb") as dst:
                import shutil

                shutil.copyfileobj(src, dst)
            target.chmod(0o600)


if __name__ == "__main__":
    if sys.argv[1] == "package":
        package()
    elif sys.argv[1] == "unpack":
        unpack(*sys.argv[2:])
    else:
        raise SystemExit("Unknown command")
