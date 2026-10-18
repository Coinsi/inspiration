"""Self-contained engineering archives; streams bytes, never extracts supplied paths."""

import copy
import hashlib
import json
import tempfile
import uuid
import zipfile
from pathlib import Path

from app.core.errors import CapabilityUnsupported, Conflict
from app.models.storage import Blob
from app.modules.engineering import packages
from app.storage import cas

FORMAT = "studio.engineering-archive@1"
MAX_BYTES = 100 * 1024**3
CHUNK = 1024**2
MEDIA_MIMES = {
    "image": {"image/png", "image/jpeg", "image/webp"},
    "video": {"video/mp4", "video/webm", "video/quicktime"},
    "audio": {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/flac"},
}


def build(db, work_id, snapshot, **options):
    """Optional restart staging is exclusive across processes on the same volume."""
    from app.modules.engineering.archive_transfer import ownership

    with ownership(options.get("checkpoint_dir")):
        return _build(db, work_id, snapshot, **options)


def _build(
    db,
    work_id,
    snapshot,
    *,
    checkpoint_dir=None,
    canceled=lambda: False,
    progress=lambda event: None,
):
    """Caller authorizes the fixed snapshot. Returned temporary file is caller-owned."""
    package = packages.export(db, work_id, snapshot)
    media = []
    for digest in package["external_media"]:
        blob = db.get(Blob, digest)
        if not blob:
            raise Conflict("工程媒体已不可用")
        media.append(
            {
                "hash": digest,
                "size": blob.size_bytes,
                "mime": blob.mime,
                "width": blob.width,
                "height": blob.height,
                "duration_ms": blob.duration_ms,
            }
        )
    if sum(m["size"] for m in media) > MAX_BYTES:
        raise CapabilityUnsupported("单次工程归档目前最多支持 100 GiB 媒体")
    from app.modules.engineering import archive_transfer

    envelope = {"format": FORMAT, "package": package, "media": media}
    staging = archive_transfer.prepare(checkpoint_dir, envelope) if checkpoint_dir else None
    total = sum(m["size"] for m in media)
    completed = 0
    with tempfile.NamedTemporaryFile(
        prefix="inspiration-package-", suffix=".zip", delete=False, dir=staging
    ) as tmp:
        path = Path(tmp.name)
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.writestr(
                "engineering.json",
                json.dumps(
                    envelope,
                    ensure_ascii=False,
                    allow_nan=False,
                ),
            )
            for entry in media:
                archive_transfer.check(canceled)
                blob = db.get(Blob, entry["hash"])
                local = None
                if staging is not None:
                    local = archive_transfer.stage(
                        staging,
                        blob,
                        entry,
                        canceled=canceled,
                        chunk_size=CHUNK,
                        progress=lambda count, base=completed: progress(
                            {
                                "phase": "staging",
                                "completed_bytes": base + count,
                                "total_bytes": total,
                            }
                        ),
                    )
                digest, size = hashlib.sha256(), 0
                with (
                    local.open("rb") if local else cas.open_range(blob) as source,
                    archive.open("media/" + blob.hash, "w", force_zip64=True) as target,
                ):
                    while chunk := source.read(CHUNK):
                        archive_transfer.check(canceled)
                        size += len(chunk)
                        if size > entry["size"]:
                            raise Conflict("媒体大小与固定版本不一致")
                        digest.update(chunk)
                        target.write(chunk)
                        progress(
                            {
                                "phase": "packing",
                                "completed_bytes": completed + size,
                                "total_bytes": total,
                            }
                        )
                if size != entry["size"] or digest.hexdigest() != entry["hash"]:
                    raise Conflict("媒体内容与固定版本不一致")
                completed += size
        archive_transfer.check(canceled)
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def inspect(path, *, canceled=lambda: False, progress=lambda event: None):
    """Validate every byte before an importer writes DB state. No filesystem extraction."""
    try:
        from app.modules.engineering.archive_transfer import check

        check(canceled)
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = [e.filename for e in entries]
            if len(names) > 2001 or len(names) != len(set(names)):
                raise ValueError("重复文件或归档条目过多")
            header = archive.getinfo("engineering.json")
            if header.file_size > 20 * 1024**2:
                raise ValueError("工程描述过大")
            envelope = json.loads(archive.read(header))
            if set(envelope) != {"format", "package", "media"} or envelope["format"] != FORMAT:
                raise ValueError("归档版本不支持")
            package, _ = packages.inspect(envelope["package"])
            media = envelope["media"]
            if not isinstance(media, list) or len(media) != len(package.external_media):
                raise ValueError("媒体清单不完整")
            hashes = [m["hash"] for m in media]
            if set(hashes) != set(package.external_media) or len(set(hashes)) != len(hashes):
                raise ValueError("媒体清单不匹配")
            if set(names) != {"engineering.json", *("media/" + h for h in hashes)}:
                raise ValueError("包含未声明的文件")
            total = 0
            for entry in media:
                check(canceled)
                if set(entry) != {"hash", "size", "mime", "width", "height", "duration_ms"}:
                    raise ValueError("媒体元数据字段不合法")
                if type(entry["size"]) is not int or entry["size"] <= 0:
                    raise ValueError("媒体大小不合法")
                if entry["mime"] not in set().union(*MEDIA_MIMES.values()):
                    raise ValueError("不支持归档中的媒体格式")
                for key in ("width", "height", "duration_ms"):
                    value = entry[key]
                    if value is not None and (
                        type(value) is not int or value < 0 or value > 2**31 - 1
                    ):
                        raise ValueError("媒体元数据不合法")
                total += entry["size"]
                if total > MAX_BYTES:
                    raise ValueError("归档超过当前容量限制")
                info = archive.getinfo("media/" + entry["hash"])
                if info.file_size != entry["size"] or info.flag_bits & 1:
                    raise ValueError("媒体大小不一致或文件被加密")
                digest = hashlib.sha256()
                size = 0
                with archive.open(info) as stream:
                    while chunk := stream.read(CHUNK):
                        check(canceled)
                        size += len(chunk)
                        if size > entry["size"]:
                            raise ValueError("媒体超过声明大小")
                        digest.update(chunk)
                        progress(
                            {"phase": "validating", "completed_bytes": total - entry["size"] + size}
                        )
                if size != entry["size"] or digest.hexdigest() != entry["hash"]:
                    raise ValueError("归档媒体校验失败")
            check(canceled)
            return envelope
    except (ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise CapabilityUnsupported("工程归档校验失败", {"reason": str(exc)}) from exc


def restore(
    db,
    ctx,
    work_id,
    path,
    request_key,
    *,
    canceled=lambda: False,
    progress=lambda event: None,
    reserve=lambda size: None,
    temp_dir=None,
):
    """Rebind imported bytes to destination-owned records; original IDs are provenance only."""
    from sqlalchemy import select

    from app.core.permissions import require
    from app.models.engineering import EngineeringChange, EngineeringDocument
    from app.models.generation import Generation, GenerationJob
    from app.modules.engineering import documents, schemas, service
    from app.modules.engineering.archive_transfer import check

    require(ctx.role, "engineering.edit")
    work = service.work(db, ctx, work_id, lock=True)
    check(canceled)

    def digest_stream(stream):
        digest = hashlib.sha256()
        while chunk := stream.read(CHUNK):
            check(canceled)
            digest.update(chunk)
        return digest.hexdigest()

    if hasattr(path, "read"):
        path.seek(0)
        archive_hash = digest_stream(path)
        path.seek(0)
    else:
        with Path(path).open("rb") as stream:
            archive_hash = digest_stream(stream)
    prior = db.scalar(
        select(EngineeringChange).where(
            EngineeringChange.work_id == work_id,
            EngineeringChange.created_by == ctx.user.id,
            EngineeringChange.request_key == request_key,
        )
    )
    if prior:
        if prior.snapshot.get("archive_hash") != archive_hash:
            raise Conflict("请求标识已用于不同的工程导入")
        return service.change_out(prior)
    if work.engineering_revision or db.scalar(
        select(EngineeringDocument.id)
        .where(EngineeringDocument.work_id == work_id, EngineeringDocument.kind != "work.manifest")
        .limit(1)
    ):
        raise Conflict("请将完整归档导入空作品")
    envelope = inspect(path, canceled=canceled, progress=progress)
    package = copy.deepcopy(envelope["package"])
    media = {m["hash"]: m for m in envelope["media"]}
    reserve(max((m["size"] for m in media.values()), default=0))
    _, order = packages.inspect(package)
    resources = [node for node in order if node.kind == "resource"]
    for node in resources:
        if media[node.payload["blob_hash"]]["mime"] not in MEDIA_MIMES[node.payload["media_type"]]:
            raise CapabilityUnsupported("媒体格式与工程声明不一致")
    # Validate the whole archive before writing any source records. Files are CAS
    # objects; a later DB rollback may leave unreferenced bytes. Physical GC is
    # intentionally not enabled yet; never infer deletability from this import alone.
    with (
        zipfile.ZipFile(path) as archive,
        tempfile.TemporaryDirectory(prefix="inspiration-import-", dir=temp_dir) as temp,
    ):
        for digest, entry in media.items():
            check(canceled)
            local = Path(temp) / digest
            with archive.open("media/" + digest) as source, local.open("wb") as target:
                while chunk := source.read(CHUNK):
                    check(canceled)
                    target.write(chunk)
            blob = cas.put_file(
                db,
                local,
                entry["mime"],
                force_store=True,
                **{k: entry[k] for k in ("width", "height", "duration_ms")},
            )
            if blob.hash != digest:
                raise Conflict("恢复媒体校验失败")
            local.unlink()
            progress({"phase": "restoring", "blob_hash": digest})
    rebound = {}
    for node in resources:
        check(canceled)
        original = node.payload
        key = (original["source_kind"], original["source_id"])
        if key in rebound:
            continue
        job = GenerationJob(
            project_id=ctx.project.id,
            target_type="work",
            target_id=work_id,
            provider="archive",
            status="succeeded",
            input_snapshot={"archive_hash": archive_hash, "operation": "archive_import"},
        )
        db.add(job)
        db.flush()
        result = Generation(
            project_id=ctx.project.id,
            job_id=job.id,
            target_type="work",
            target_id=work_id,
            provider="archive",
            output_type=original["media_type"],
            output_blob_hash=original["blob_hash"],
            input_refs={"archive_hash": archive_hash, "original_source": original},
        )
        db.add(result)
        db.flush()
        rebound[key] = str(result.id)
    for node in package["documents"]:
        if node["kind"] == "resource":
            old = node["payload"]
            node["payload"] = {
                **old,
                "source_kind": "generation",
                "source_id": rebound[(old["source_kind"], old["source_id"])],
                "resource_id": str(uuid.uuid4()),
            }
        elif node["kind"] == "sequence.timeline":
            editor = node["payload"]["editor"]
            for collection in ("items", "audio", "visuals"):
                for item in editor[collection]:
                    replacement = rebound.get(("generation", item["generation_id"]))
                    if not replacement:
                        raise Conflict("剪辑缺少固定媒体依赖")
                    item["generation_id"] = replacement
                    if collection == "items":
                        item["shot_id"] = None
            # Transcript evidence belongs to the originating project. Keep it as
            # provenance in the imported package, not as trusted local approval.
            for cue in editor["subtitles"]:
                cue.pop("source", None)
        node["content_hash"] = documents.digest(node["inputs"], node["payload"])
    check(canceled)
    response = service.propose(
        db,
        ctx,
        work_id,
        schemas.Propose(
            request_key=request_key,
            base_revision=0,
            commands=[schemas.ImportPackage(operation="package.import", package=package)],
        ),
    )
    change = db.get(EngineeringChange, uuid.UUID(str(response["id"])))
    change.snapshot = {
        **change.snapshot,
        "archive_hash": archive_hash,
        "archive_provenance": envelope["package"],
    }
    db.flush()
    return service.change_out(change)
