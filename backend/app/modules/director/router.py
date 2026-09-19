import hashlib
import io
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.core.errors import CapabilityUnsupported, Conflict, Locked, NotFound
from app.models.director import DirectorReference, DirectorRevision, DirectorScene
from app.models.generation import Generation, GenerationJob
from app.models.shot import Shot
from app.modules.director import schemas
from app.modules.generation.schemas import GenerationOut
from app.storage import cas

router = APIRouter(prefix="/projects/{project_id}/director-scenes", tags=["director"])
DB = Depends(get_db, scope="function")
READ = Depends(get_project_context)
EDIT = Depends(require_action("director.edit"))
EXPORT = Depends(require_action("generation.trigger"))
FILE = File(..., alias="file")
FRAMES = File(..., alias="frames")
SHOT_ID = Form(..., alias="shot_id")
REQUEST_KEY = Form(..., alias="request_key")


def get(db, ctx, id, lock=False):
    q = select(DirectorScene).where(
        DirectorScene.id == id, DirectorScene.project_id == ctx.project.id
    )
    c = db.scalar(q.with_for_update() if lock else q)
    if not c:
        raise NotFound("预演场景不存在")
    return c


def out(c):
    return {"id": c.id, "name": c.name, "revision": c.revision, "document": c.document}


def remember(db, ctx, c):
    db.add(
        DirectorRevision(
            scene_id=c.id,
            revision=c.revision,
            document={"name": c.name, "document": c.document},
            created_by=ctx.user.id,
        )
    )
    audit.record(
        db,
        action="director.save",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="director",
        target_id=c.id,
        detail={"revision": c.revision},
    )


@router.get("")
def listing(ctx: ProjectContext = READ, db: Session = DB, offset: int = Query(0, ge=0)):
    return [
        {"id": c.id, "name": c.name, "revision": c.revision}
        for c in db.scalars(
            select(DirectorScene)
            .where(DirectorScene.project_id == ctx.project.id)
            .order_by(DirectorScene.created_at.desc(), DirectorScene.id)
            .offset(offset)
            .limit(50)
        )
    ]


@router.post("")
def create(data: schemas.Create, ctx: ProjectContext = EDIT, db: Session = DB):
    c = DirectorScene(
        project_id=ctx.project.id,
        name=data.name,
        revision=0,
        document=schemas.Document().model_dump(mode="json"),
        created_by=ctx.user.id,
    )
    db.add(c)
    db.flush()
    remember(db, ctx, c)
    return out(c)


@router.get("/{scene_id}")
def read(scene_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB):
    return out(get(db, ctx, scene_id))


@router.put("/{scene_id}")
def save(scene_id: uuid.UUID, data: schemas.Save, ctx: ProjectContext = EDIT, db: Session = DB):
    c = get(db, ctx, scene_id, True)
    if c.revision != data.revision:
        raise Conflict("场景已有新版本，请保留草稿后重新载入")
    c.name = data.name
    c.document = data.document.model_dump(mode="json")
    c.revision += 1
    remember(db, ctx, c)
    db.flush()
    return out(c)


@router.get("/{scene_id}/history")
def history(
    scene_id: uuid.UUID, ctx: ProjectContext = READ, db: Session = DB, offset: int = Query(0, ge=0)
):
    get(db, ctx, scene_id)
    return [
        {"revision": r.revision, "created_at": r.created_at}
        for r in db.scalars(
            select(DirectorRevision)
            .where(DirectorRevision.scene_id == scene_id)
            .order_by(DirectorRevision.revision.desc())
            .offset(offset)
            .limit(50)
        )
    ]


@router.post("/{scene_id}/history/{revision}/restore")
def restore(
    scene_id: uuid.UUID,
    revision: int,
    data: schemas.Revision,
    ctx: ProjectContext = EDIT,
    db: Session = DB,
):
    c = get(db, ctx, scene_id, True)
    if c.revision != data.revision:
        raise Conflict("场景已有新版本")
    r = db.scalar(
        select(DirectorRevision).where(
            DirectorRevision.scene_id == scene_id, DirectorRevision.revision == revision
        )
    )
    if not r:
        raise NotFound("历史版本不存在")
    return save(scene_id, schemas.Save(revision=c.revision, **r.document), ctx, db)


@router.post("/{scene_id}/previsualization")
def previsualization(
    scene_id: uuid.UUID,
    revision: int = Form(..., ge=0),
    frames: list[UploadFile] = FRAMES,
    ctx: ProjectContext = EXPORT,
    db: Session = DB,
):
    import math
    import tempfile
    from pathlib import Path

    from app.modules.generation import media_engine

    c = get(db, ctx, scene_id)
    if c.revision != revision:
        raise Conflict("请保存场景后重新导出")
    duration = c.document["duration"]
    count = math.ceil(duration * 12)
    if len(frames) != count or count > 180:
        raise CapabilityUnsupported("预演帧数量与场景时长不一致")
    size = 0
    dimensions = None
    with tempfile.TemporaryDirectory(prefix="director-") as directory:
        path = Path(directory)
        for i, file in enumerate(frames):
            data = file.file.read(1024 * 1024 + 1)
            size += len(data)
            if len(data) > 1024 * 1024 or size > 32 * 1024 * 1024:
                raise CapabilityUnsupported("预演画面超过32MB限制，请简化布景")
            try:
                with Image.open(io.BytesIO(data)) as im:
                    if im.format != "JPEG" or not (
                        64 <= im.width <= 1280 and 64 <= im.height <= 1280
                    ):
                        raise ValueError()
                    if dimensions and dimensions != im.size:
                        raise ValueError()
                    dimensions = im.size
                    im.load()
                    im.convert("RGB").save(path / f"{i:04d}.jpg", quality=90)
            except (ValueError, OSError, Image.DecompressionBombError) as exc:
                raise CapabilityUnsupported("预演包含无效或尺寸不一致的画面") from exc
        try:
            media_engine.run(
                [
                    "-framerate",
                    "12",
                    "-i",
                    str(path / "%04d.jpg"),
                    "-t",
                    str(duration),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-pix_fmt",
                    "yuv420p",
                    "-vf",
                    "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-movflags",
                    "+faststart",
                    str(path / "preview.mp4"),
                ]
            )
        except ValueError as exc:
            raise CapabilityUnsupported("预演视频合成失败，请重试") from exc
        output = (path / "preview.mp4").read_bytes()
    audit.record(
        db,
        action="director.previsualization",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="director",
        target_id=c.id,
        detail={"revision": revision, "frames": count, "duration": duration},
    )
    return Response(
        output,
        media_type="video/mp4",
        headers={"Content-Disposition": 'attachment; filename="director-preview.mp4"'},
    )


@router.post("/{scene_id}/references", response_model=GenerationOut)
def export(
    scene_id: uuid.UUID,
    revision: int = Form(..., ge=0),
    shot_id: uuid.UUID = SHOT_ID,
    request_key: uuid.UUID = REQUEST_KEY,
    progress: float = Form(0, ge=0, le=1),
    file: UploadFile = FILE,
    ctx: ProjectContext = EXPORT,
    db: Session = DB,
):
    c = get(db, ctx, scene_id, True)
    data = file.file.read(8 * 1024 * 1024 + 1)
    if not data or len(data) > 8 * 1024 * 1024:
        raise CapabilityUnsupported("参考画面不得超过8MB")
    fingerprint = hashlib.sha256(f"{revision}:{shot_id}:{progress}:".encode() + data).hexdigest()
    old = db.scalar(
        select(DirectorReference).where(
            DirectorReference.scene_id == scene_id, DirectorReference.request_key == request_key
        )
    )
    if old:
        if old.fingerprint != fingerprint:
            raise Conflict("同一请求编号不能用于另一张画面")
        return db.get(Generation, old.generation_id)
    if c.revision != revision:
        raise Conflict("请先保存当前场景，再导出参考")
    shot = db.scalar(
        select(Shot)
        .where(Shot.id == shot_id, Shot.project_id == ctx.project.id, Shot.deleted_at.is_(None))
        .with_for_update()
    )
    if not shot:
        raise NotFound("镜头不存在")
    if shot.status == "locked":
        raise Locked("已锁定的镜头不能增加参考")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format != "PNG" or not (64 <= im.width <= 2048 and 64 <= im.height <= 2048):
                raise ValueError()
            im.load()
            clean = io.BytesIO()
            im.convert("RGB").save(clean, format="PNG")
            width, height = im.size
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise CapabilityUnsupported("请上传有效的PNG取景画面（64至2048像素）") from exc
    blob = cas.put_bytes(db, clean.getvalue(), "image/png", width=width, height=height)
    origin = {
        "operation": "director_reference",
        "scene_id": str(c.id),
        "revision": revision,
        "progress": progress,
        "document": c.document,
        "render_source": "browser_webgl",
    }
    job = GenerationJob(
        project_id=ctx.project.id,
        target_type="shot",
        target_id=shot_id,
        provider="local",
        request_type="image",
        status="succeeded",
        estimated_cost=0,
        actual_cost=0,
        created_by=ctx.user.id,
        input_snapshot=origin,
    )
    db.add(job)
    db.flush()
    gen = Generation(
        project_id=ctx.project.id,
        job_id=job.id,
        target_type="shot",
        target_id=shot_id,
        provider="local",
        output_type="image",
        output_blob_hash=blob.hash,
        cost_points=0,
        input_refs=origin,
    )
    db.add(gen)
    db.flush()
    db.add(
        DirectorReference(
            scene_id=scene_id,
            request_key=request_key,
            fingerprint=fingerprint,
            generation_id=gen.id,
        )
    )
    audit.record(
        db,
        action="director.reference",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type="shot",
        target_id=shot_id,
        detail={"scene_id": str(scene_id), "revision": revision, "generation_id": str(gen.id)},
    )
    return gen
