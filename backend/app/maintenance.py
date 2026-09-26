"""Offline backup and integrity checks. Run from backend; never deletes source data.

python -m app.maintenance backup DEST --pg-dump /path/to/pg_dump
python -m app.maintenance verify DEST
python -m app.maintenance inventory
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.models.storage import Blob
from app.storage import cas


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def database_args(program):
    url = make_url(settings.database_url)
    env = dict(os.environ, PGPASSWORD=url.password or "")
    # libpq options such as sslmode/options are passed without exposing credentials.
    for key in ("sslmode", "sslrootcert", "sslcert", "sslkey", "options"):
        if key in url.query:
            env["PG" + key.upper()] = str(url.query[key])
    return [
        program,
        "-h",
        url.host or "localhost",
        "-p",
        str(url.port or 5432),
        "-U",
        url.username or "",
        "-d",
        url.database or "",
    ], env


def backup(destination, pg_dump="pg_dump", database_only=False, allow_missing=False):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {
        "format": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "media_included": not database_only,
        "files": [],
        "missing": [],
    }
    # Keep the same MVCC snapshot for database dump and immutable blob inventory.
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        with conn.begin():
            conn.execute(text("SET TRANSACTION READ ONLY"))
            schema = conn.scalar(text("SELECT current_schema()"))
            snapshot = conn.scalar(text("SELECT pg_export_snapshot()"))
            manifest["schema"] = schema
            args, env = database_args(pg_dump)
            dump = destination / "database.dump"
            result = subprocess.run(
                [
                    *args,
                    "-Fc",
                    "--no-owner",
                    "--no-acl",
                    "-n",
                    schema,
                    "--snapshot",
                    snapshot,
                    "-f",
                    str(dump),
                ],
                env=env,
                capture_output=True,
            )
            if result.returncode:
                raise RuntimeError(
                    "数据库备份失败；目录不含完成标记，请检查 pg_dump 版本和连接权限"
                )
            manifest["files"].append(
                {"path": dump.name, "sha256": digest(dump), "bytes": dump.stat().st_size}
            )
            if not database_only:
                from sqlalchemy.orm import Session

                with Session(bind=conn) as db:
                    for blob in db.scalars(select(Blob).execution_options(yield_per=100)):
                        target = destination / "objects" / blob.hash[:2] / blob.hash
                        target.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            with cas.open_range(blob) as source, target.open("xb") as output:
                                shutil.copyfileobj(source, output, 1024 * 1024)
                            actual = digest(target)
                            if actual != blob.hash or target.stat().st_size != blob.size_bytes:
                                raise ValueError("素材备份校验失败，未生成完成标记")
                        except Exception:
                            if not allow_missing:
                                raise
                            manifest["missing"].append(
                                {"hash": blob.hash, "bytes": blob.size_bytes}
                            )
                            continue
                        manifest["files"].append(
                            {
                                "path": target.relative_to(destination).as_posix(),
                                "sha256": actual,
                                "bytes": blob.size_bytes,
                            }
                        )
    # Presence of manifest is the completion marker; incomplete directories aren't backups.
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return verify(destination)


def verify(destination):
    root = Path(destination).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or not manifest.get("files"):
        raise ValueError("不支持或不完整的备份清单")
    if not any(f["path"] == "database.dump" for f in manifest["files"]):
        raise ValueError("缺少数据库备份")
    for entry in manifest["files"]:
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("备份文件缺失或路径非法")
        if path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
            raise ValueError("备份校验失败: " + entry["path"])
    return {
        "status": "partial" if manifest.get("missing") else "verified",
        "files": len(manifest["files"]),
        "media_included": manifest["media_included"],
        "missing": len(manifest.get("missing", [])),
    }


def inventory():
    with SessionLocal() as db:
        count, size = db.execute(
            select(func.count(Blob.hash), func.coalesce(func.sum(Blob.size_bytes), 0))
        ).one()
        kinds = db.execute(
            select(Blob.mime, func.count(), func.sum(Blob.size_bytes)).group_by(Blob.mime)
        ).all()
    return {
        "objects": count,
        "stored_bytes": size,
        "by_mime": [{"mime": mime, "objects": n, "bytes": b} for mime, n, b in kinds],
        "note": "去重后的当前数据库登记量，包含回收站和历史版本；不等于可回收空间。",
    }


def audit_storage():
    problems = []
    checked = 0
    with SessionLocal() as db:
        for blob in db.scalars(select(Blob).execution_options(yield_per=100)):
            checked += 1
            hasher, size = hashlib.sha256(), 0
            try:
                with cas.open_range(blob) as stream:
                    while chunk := stream.read(1024 * 1024):
                        hasher.update(chunk)
                        size += len(chunk)
                status = (
                    "ok"
                    if size == blob.size_bytes and hasher.hexdigest() == blob.hash
                    else "mismatch"
                )
            except Exception:
                status = "unavailable"
            if status != "ok":
                problems.append({"hash": blob.hash, "bytes": blob.size_bytes, "status": status})
    return {"checked": checked, "ok": checked - len(problems), "problems": problems}


def restore_objects(destination):
    """After restoring the dump into an isolated database, rebind its immutable objects."""
    root = Path(destination).resolve()
    verified = verify(root)
    if not verified["media_included"] or verified["missing"]:
        raise ValueError("此备份缺少素材文件，不能执行完整恢复")
    with SessionLocal() as db:
        # Check every required object before changing a single database URI.
        blobs = list(db.scalars(select(Blob)))
        for blob in blobs:
            path = root / "objects" / blob.hash[:2] / blob.hash
            if not path.is_file() or digest(path) != blob.hash:
                raise ValueError("数据库与素材备份不匹配；未修改存储地址")
        for blob in blobs:
            path = root / "objects" / blob.hash[:2] / blob.hash
            cas.put_file(db, path, blob.mime, force_store=True)
        db.commit()
    return {"status": "restored", "objects": len(blobs)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    save = sub.add_parser("backup")
    save.add_argument("destination")
    save.add_argument("--pg-dump", default="pg_dump")
    save.add_argument("--database-only", action="store_true")
    save.add_argument(
        "--allow-missing",
        action="store_true",
        help="保留可读素材并显式记录缺失；结果标记为 partial，不能用于完整恢复",
    )
    check = sub.add_parser("verify")
    check.add_argument("destination")
    restore = sub.add_parser("restore-objects")
    restore.add_argument("destination")
    sub.add_parser("inventory")
    sub.add_parser("audit")
    args = parser.parse_args()
    if args.command == "backup":
        result = backup(args.destination, args.pg_dump, args.database_only, args.allow_missing)
    elif args.command == "verify":
        result = verify(args.destination)
    elif args.command == "restore-objects":
        result = restore_objects(args.destination)
    elif args.command == "audit":
        result = audit_storage()
    else:
        result = inventory()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
