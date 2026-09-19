"""narrative 路由(对照 04 §3.3)。"""

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.modules.asset import schemas as asset_schemas
from app.modules.narrative import schemas, service

router = APIRouter(prefix="/projects/{project_id}", tags=["narrative"])

EDIT = require_action("narrative.edit")
ASSET_EDIT = require_action("asset.edit")


# ── 小说 ──
@router.post("/novels/import", response_model=schemas.NovelOut)
def import_novel(
    file: UploadFile = File(...),
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    data = file.file.read()
    return service.import_novel(
        db, ctx, file.filename or "novel.txt", file.content_type or "text/plain", data
    )


@router.get("/novels", response_model=list[schemas.NovelOut])
def list_novels(
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.list_novels(db, ctx.project.id)


# 注意:必须声明在 /novels/{novel_id} 之前,否则 "trash" 会被当作 novel_id
@router.get("/novels/trash", response_model=list[schemas.NovelOut])
def list_novel_trash(
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.list_deleted_novels(db, ctx.project.id)


@router.get("/novels/{novel_id}", response_model=schemas.NovelDetailOut)
def get_novel(
    novel_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    n = service.get_novel(db, ctx.project.id, novel_id)
    out = schemas.NovelDetailOut.model_validate(n)
    out.chapters = [schemas.ChapterOut.model_validate(c) for c in service.chapters_of(db, novel_id)]
    return out


@router.patch("/novels/{novel_id}", response_model=schemas.NovelOut)
def rename_novel(
    novel_id: uuid.UUID,
    data: schemas.NovelUpdate,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.rename_novel(db, ctx, novel_id, data.title)


@router.delete("/novels/{novel_id}", status_code=204)
def delete_novel(
    novel_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    service.delete_novel(db, ctx, novel_id)


@router.post("/novels/{novel_id}/restore", response_model=schemas.NovelOut)
def restore_novel(
    novel_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.restore_novel(db, ctx, novel_id)


# ── 剧本 / 场次 ──
@router.post("/scripts", response_model=schemas.ScriptOut)
def create_script(
    data: schemas.ScriptIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.create_script(db, ctx, data.title)


@router.get("/scripts", response_model=list[schemas.ScriptOut])
def list_scripts(
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    out = []
    for s in service.list_scripts(db, ctx.project.id):
        o = schemas.ScriptOut.model_validate(s)
        blocks = s.content_blocks or []
        o.block_count = len(blocks)
        o.scene_count = sum(1 for b in blocks if b.get("block_type") == "scene_heading")
        out.append(o)
    return out


# ── 剧本正文(typed blocks):小说→剧本、读取、编辑保存、导出 ──
@router.post("/scripts/from-chapter", response_model=schemas.ScriptDetailOut)
def generate_script_from_chapter(
    data: schemas.GenerateScriptIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.generate_script_from_chapter(db, ctx, data)


@router.get("/scripts/{script_id}", response_model=schemas.ScriptDetailOut)
def get_script(
    script_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.get_script(db, ctx.project.id, script_id)


@router.delete("/scripts/{script_id}", status_code=204)
def delete_script(
    script_id: uuid.UUID,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    service.delete_script(db, ctx, script_id)


@router.put("/scripts/{script_id}/blocks", response_model=schemas.ScriptDetailOut)
def update_script_blocks(
    script_id: uuid.UUID,
    data: schemas.UpdateScriptBlocksIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.update_script_blocks(db, ctx, script_id, data.blocks, data.expected_revision)


@router.get("/scripts/{script_id}/stats")
def script_stats(
    script_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    """剧本数据:本剧本已落库的场次/分镜数 + 项目资产分类计数。"""
    return service.script_stats(db, ctx.project.id, script_id)


@router.get("/scripts/{script_id}/fountain", response_class=PlainTextResponse)
def export_script_fountain(
    script_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.export_script_fountain(db, ctx.project.id, script_id)


@router.post("/scripts/{script_id}/decompose", response_model=schemas.DecomposeOut)
def decompose_script(
    script_id: uuid.UUID,
    strategy: str | None = Query(None),
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    """从剧本正文派生场次/镜头建议(按剧本自身场景头切分);随后用 apply-scenes 落库。"""
    return service.decompose_script(db, ctx.project.id, script_id, strategy)


@router.get("/scripts/{script_id}/scenes", response_model=list[schemas.SceneOut])
def list_scenes(
    script_id: uuid.UUID,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.list_scenes(db, ctx.project.id, script_id)


@router.get("/scenes", response_model=list[schemas.SceneListItem])
def list_all_scenes(
    novel_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db, scope="function"),
):
    return service.list_all_scenes(db, ctx.project.id, novel_id, chapter_id)


# ── AI 拆解 ──
@router.post("/novels/decompose", response_model=schemas.DecomposeOut)
def decompose(
    chapter_id: uuid.UUID = Query(...),
    strategy: str | None = Query(None),
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.decompose_chapter(db, ctx.project.id, chapter_id, strategy)


@router.post("/scripts/{script_id}/apply-scenes", response_model=list[schemas.SceneOut])
def apply_scenes(
    script_id: uuid.UUID,
    data: schemas.ApplyScenesIn,
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.apply_scenes(db, ctx, script_id, data)


@router.post("/extract-entities", response_model=list[schemas.EntityDraftOut])
def extract_entities(
    data: schemas.ExtractEntitiesIn,
    strategy: str | None = Query(None),
    ctx: ProjectContext = Depends(EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    return service.extract_entities(db, ctx.project.id, data, strategy)


@router.post("/apply-entities", response_model=list[asset_schemas.AssetOut])
def apply_entities(
    data: schemas.ApplyEntitiesIn,
    ctx: ProjectContext = Depends(ASSET_EDIT),
    db: Session = Depends(get_db, scope="function"),
):
    assets = service.apply_entities(db, ctx, data)
    return [asset_schemas.AssetOut.of(a) for a in assets]
