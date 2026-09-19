"""One durable scheduling step per transaction; generation owns external execution."""

import copy
import logging
import threading
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.adapters.contracts import ReferenceImage
from app.core.database import SessionLocal
from app.core.deps import ProjectContext
from app.core.errors import AppError, Forbidden
from app.core.permissions import require
from app.models.canvas import CanvasRun
from app.models.generation import Generation, GenerationJob
from app.models.identity import Membership, Project, User
from app.modules.canvas.service import graph_order
from app.modules.generation import jobs, schemas, service

log = logging.getLogger(__name__)


def advance(db, run_id):
    # Row lock is held through job preparation. Mapping + job become durable in
    # dispatch's single commit, before any provider is invoked. SKIP LOCKED lets
    # other workers schedule independent batches, never the same node twice.
    r = db.scalar(
        select(CanvasRun)
        .where(CanvasRun.id == run_id, CanvasRun.status == "running")
        .with_for_update(skip_locked=True)
    )
    if not r:
        return False
    steps = copy.deepcopy(r.steps)
    current = None
    try:
        project = db.get(Project, r.project_id)
        user = db.get(User, r.created_by)
        member = db.scalar(
            select(Membership).where(
                Membership.project_id == r.project_id, Membership.user_id == r.created_by
            )
        )
        if not project or project.deleted_at or not user or not user.is_active or not member:
            raise Forbidden("发起者或项目已不可用")
        ctx = ProjectContext(project, member, user)
        require(ctx.role, "generation.trigger")
        nodes = {n["id"]: n for n in r.snapshot["nodes"]}
        for node_id in graph_order(r.snapshot):
            step = steps[node_id]
            current = node_id
            if step["status"] == "succeeded":
                continue
            if step.get("job_id"):
                job = db.get(GenerationJob, uuid.UUID(step["job_id"]))
                if not job:
                    raise RuntimeError("Missing durable job")
                if job.status in jobs.ACTIVE:
                    r.updated_at = datetime.now(UTC)
                    db.commit()
                    return False
                if job.status != "succeeded":
                    step.update(status="failed", error=job.error or "生成任务已取消")
                    r.steps = steps
                    r.status = "failed"
                    r.error = step["error"]
                    db.commit()
                    return True
                outputs = list(
                    db.scalars(
                        select(Generation)
                        .where(Generation.job_id == job.id)
                        .order_by(
                            Generation.input_refs["variant_index"].as_integer(),
                            Generation.created_at,
                            Generation.id,
                        )
                    )
                )
                if not outputs:
                    raise RuntimeError("Job has no outputs")
                step.update(
                    status="succeeded",
                    generation_ids=[str(g.id) for g in outputs],
                    output={
                        "text": job.input_snapshot.get("prompt", ""),
                        "blob_hash": outputs[0].output_blob_hash
                        if outputs[0].output_type == "image"
                        else None,
                        "generation_id": str(outputs[0].id),
                    },
                )
                r.steps = steps
                db.commit()
                return True
            n = nodes[node_id]
            d = n["data"]
            incoming = [e for e in r.snapshot["edges"] if e["target"] == node_id]
            if any(steps[e["source"]]["status"] != "succeeded" for e in incoming):
                continue
            if d["kind"] != "generate":
                step.update(status="succeeded", output=r.snapshot["resolved"][node_id])
                r.steps = steps
                db.commit()
                return True
            text = [
                steps[e["source"]]["output"]["text"]
                for e in incoming
                if e["targetHandle"] == "prompt"
            ]
            refs = []
            for e in incoming:
                if e["targetHandle"] == "reference":
                    output = steps[e["source"]]["output"]
                    if not output.get("blob_hash"):
                        raise AppError("上游没有可用的参考图片")
                    refs.append(ReferenceImage(blob_hash=output["blob_hash"], role="reference"))
            generation = schemas.GenerateIn.model_validate(d["generate"])
            own_text = (
                generation.prompt_override or r.snapshot["resolved"][node_id]["text"] or d["text"]
            )
            image_sources = [e["source"] for e in incoming if e["targetHandle"] == "reference"]
            for mention in d.get("mentions", []):
                source_id = mention["node_id"]
                replacement = (
                    f"[参考图{image_sources.index(source_id) + 1}：{mention['alias']}]"
                    if source_id in image_sources
                    else mention["alias"]
                )
                own_text = own_text.replace(f"@【{mention['alias']}】", replacement)
            generation.prompt_override = "\n".join(filter(None, [*text, own_text]))
            if len(generation.prompt_override) > 30000:
                raise AppError("合并后的提示词过长，请缩短输入")
            # Existing asset reference collection would float with later edits.
            # Canvas only passes explicit frozen references and explicit controls.
            generation.use_references = False
            job = service.submit(
                db,
                ctx,
                d["target_type"],
                uuid.UUID(d["target_id"]),
                generation,
                extra_references=refs,
                skill_snapshot=r.snapshot.get("skill_snapshots", {}).get(node_id),
                project_guidance=r.snapshot.get("project_guidance"),
                defer_dispatch=True,
                execution_origin={
                    "run_id": str(r.id),
                    "node_id": node_id,
                    "attempt": step["attempt"] + 1,
                },
            )
            step.update(status="running", job_id=str(job.id), attempt=step["attempt"] + 1)
            r.steps = steps
            jobs.dispatch(db, job)
            return True
        r.status = "succeeded"
        r.steps = steps
        db.commit()
        return True
    except Exception as exc:
        # Provider dispatch commits before invocation; never erase a durable job
        # mapping on an exception after that boundary. The next tick reconciles it.
        db.rollback()
        r = db.scalar(select(CanvasRun).where(CanvasRun.id == run_id).with_for_update())
        if not r or r.status != "running":
            return False
        durable = copy.deepcopy(r.steps)
        if current and durable[current].get("job_id"):
            job = db.get(GenerationJob, uuid.UUID(durable[current]["job_id"]))
            has_outputs = job and db.scalar(
                select(Generation.id).where(Generation.job_id == job.id).limit(1)
            )
            if job and (job.status in jobs.ACTIVE or has_outputs):
                db.commit()
                log.warning("Canvas job reconciliation deferred: %s", type(exc).__name__)
                return False
        message = (
            exc.message
            if isinstance(exc, AppError)
            else "节点准备失败，请检查目标对象和供应商配置后重试"
        )
        if current:
            durable[current].update(status="failed", error=message)
        r.steps = durable
        r.status = "failed"
        r.error = message[:1000]
        db.commit()
        return True


def run_once():
    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(CanvasRun.id)
                .where(CanvasRun.status == "running")
                .order_by(CanvasRun.updated_at)
                .limit(20)
            )
        )
    worked = False
    for run_id in ids:
        with SessionLocal() as db:
            worked = advance(db, run_id) or worked
    return worked


def start():
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                worked = run_once()
            except Exception:
                log.warning("Canvas queue unavailable; check migrations")
                worked = False
            stop.wait(0.25 if worked else 2)

    thread = threading.Thread(target=loop, name="canvas-queue", daemon=True)
    thread.start()

    def shutdown():
        stop.set()
        thread.join(timeout=5)

    return shutdown
