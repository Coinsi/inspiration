"""Lossless grid crops with immutable provenance and retry-safe creation."""

import io

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from app.core import audit
from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.generation import Generation, GenerationJob
from app.models.storage import Blob
from app.modules.generation import jobs
from app.storage import cas


def crop_grid(raw, rows, columns, cells):
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > 40_000_000 or max(source.size) > 8192:
                raise CapabilityUnsupported(
                    "图片拆分支持最长边 8192 像素、总计 4000 万像素以内的图片"
                )
            if getattr(source, "n_frames", 1) > 1:
                raise CapabilityUnsupported("请先把动态图转换成静态图片")
            image = ImageOps.exif_transpose(source).convert("RGBA")
        width, height = image.size
        if width < columns or height < rows:
            raise CapabilityUnsupported("图片尺寸不足以拆分成所选行列")
        outputs = []
        for index in cells:
            row, column = divmod(index, columns)
            # Integer boundaries cover every pixel once, including odd image sizes.
            box = (
                column * width // columns,
                row * height // rows,
                (column + 1) * width // columns,
                (row + 1) * height // rows,
            )
            out = io.BytesIO()
            image.crop(box).save(out, "PNG")
            outputs.append((index, box, out.getvalue()))
        return outputs
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise CapabilityUnsupported("无法读取这张图片，请重新导入有效的静态图片") from exc


def split(db, ctx, gen_id, data):
    source = jobs.source(db, ctx.project.id, gen_id, True)
    if source.target_type not in ("asset", "shot"):
        raise CapabilityUnsupported("请选择资产或镜头中的图片")
    jobs.validate_target(db, ctx.project.id, source.target_type, source.target_id)
    cells = sorted(data.cells)
    if (
        data.rows * data.columns < 2
        or len(cells) != len(set(cells))
        or any(cell >= data.rows * data.columns for cell in cells)
    ):
        raise CapabilityUnsupported("请设置至少两格，并选择有效且不重复的格子")
    # Serialize requests on this immutable source so concurrent retries cannot duplicate versions.
    db.scalar(select(Generation).where(Generation.id == gen_id).with_for_update())
    options = {"rows": data.rows, "columns": data.columns, "cells": cells}
    previous = db.scalar(
        select(GenerationJob).where(
            GenerationJob.project_id == ctx.project.id,
            GenerationJob.input_snapshot["operation"].astext == "grid_split",
            GenerationJob.input_snapshot["source_generation_id"].astext == str(gen_id),
            GenerationJob.input_snapshot["request_key"].astext == str(data.request_key),
        )
    )
    if previous:
        if previous.params != options:
            raise Conflict("这次拆分请求的设置已改变，请重新开始拆分")
        return sorted(
            db.scalars(select(Generation).where(Generation.job_id == previous.id)),
            key=lambda g: g.input_refs["cell_index"],
        )
    blob = db.get(Blob, source.output_blob_hash)
    if not blob:
        raise NotFound("原图文件不存在")
    outputs = crop_grid(cas.read_bytes(blob), data.rows, data.columns, cells)
    snapshot = {
        "operation": "grid_split",
        "source_generation_id": str(source.id),
        "source_blob": source.output_blob_hash,
        "request_key": str(data.request_key),
        **options,
    }
    job = GenerationJob(
        project_id=ctx.project.id,
        target_type=source.target_type,
        target_id=source.target_id,
        provider="local",
        request_type="image",
        status="succeeded",
        estimated_cost=0,
        actual_cost=0,
        created_by=ctx.user.id,
        params=options,
        input_snapshot=snapshot,
    )
    jobs.event(job, "succeeded", f"图片已拆分为 {len(outputs)} 个独立版本")
    db.add(job)
    db.flush()
    result = []
    for index, box, raw in outputs:
        output = cas.put_bytes(db, raw, "image/png")
        generation = Generation(
            project_id=ctx.project.id,
            job_id=job.id,
            target_type=source.target_type,
            target_id=source.target_id,
            provider="local",
            output_type="image",
            output_blob_hash=output.hash,
            cost_points=0,
            prompt_snapshot=source.prompt_snapshot,
            input_refs={**snapshot, "cell_index": index, "crop_box": list(box)},
        )
        db.add(generation)
        result.append(generation)
    audit.record(
        db,
        action="media.grid_split",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_type=source.target_type,
        target_id=source.target_id,
        detail={"job": str(job.id), "source_generation_id": str(source.id), **options},
    )
    db.flush()
    return result
