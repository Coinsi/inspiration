"""Project task-center views for story production. Lists contain only compact progress."""

from sqlalchemy import String, and_, case, cast, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH

from app.core.permissions import can
from app.models.narrative import (
    Chapter,
    CreativeToolTask,
    Novel,
    Scene,
    Script,
    ScriptGenerationTask,
)
from app.modules.narrative import scene_progress


def _breakdown_counts(task):
    saved = task.result["progress"]

    def count(path):
        return func.jsonb_array_length(
            func.jsonb_path_query_array(task.result, cast(path, JSONPATH))
        )

    scene_path = "$.output.scenes[*]"
    success = scene_path + " ? (@.shots.size() > 0 && (!exists(@.error) || @.error == null))"
    failure = (
        scene_path
        + f' ? (@.error.type() == "string" && @.error != "{scene_progress.PENDING_MESSAGE}")'
    )
    shot_failures = count('$.output[*] ? (@.storyboard.breakdown_error.type() == "string")')
    shot_array = func.jsonb_typeof(task.result["output"]) == "array"
    shot_count = case((shot_array, func.jsonb_array_length(task.result["output"])), else_=0)
    is_shots = task.operation == "scene_breakdown"
    completed = func.coalesce(
        saved["completed"].as_integer(),
        case((is_shots, shot_count - shot_failures), else_=count(success)),
    )
    failed = func.coalesce(
        saved["failed"].as_integer(), case((is_shots, shot_failures), else_=count(failure))
    )
    # Only the pre-progress receipts need JSON-path counts; never ship raw prompts in a list.
    legacy = case(
        (
            task.operation.in_(scene_progress.OPERATIONS)
            & task.result["output"]["scenes"].is_not(None),
            func.jsonb_build_object(
                "total",
                count(scene_path),
                "completed",
                completed,
                "failed",
                failed,
                "shots",
                count(success + ".shots[*]"),
                "attempt",
                1,
                "preserved",
                0,
            ),
        ),
        (
            is_shots & shot_array,
            func.jsonb_build_object(
                "unit", "shot", "total", shot_count, "completed", completed, "failed", failed
            ),
        ),
        else_=literal(None, JSONB),
    )
    return func.coalesce(saved, legacy), completed, failed


def story_queries(ctx, projection):
    task, adaptation = CreativeToolTask, ScriptGenerationTask
    pid = ctx.project.id
    chapters = (
        select(
            Chapter.id,
            func.coalesce(Chapter.title, "第 " + cast(Chapter.ordinal, String) + " 章").label(
                "title"
            ),
            Novel.deleted_at.is_(None).label("available"),
        )
        .join(Novel, Novel.id == Chapter.novel_id)
        .where(Novel.project_id == pid)
        .subquery()
    )
    scripts = task.operation.in_(("script_decompose", "script_entities"))
    is_scene = task.operation == "scene_breakdown"
    allowed = []
    if can(ctx.role, "narrative.edit"):
        allowed.extend(("script_decompose", "script_entities", "chapter_decompose"))
    if can(ctx.role, "shot.edit"):
        allowed.append("scene_breakdown")
    summary, completed, failed = _breakdown_counts(task)
    status = case(
        (task.status == "queued", "pending"),
        (
            and_(
                task.status.in_(("succeeded", "applied")),
                task.operation.in_((*scene_progress.OPERATIONS, "scene_breakdown")),
                failed > 0,
            ),
            case((completed > 0, "partial"), else_="failed"),
        ),
        (task.status == "applied", "succeeded"),
        else_=task.status,
    )
    creative = (
        projection(
            task,
            "creative",
            func.coalesce(Script.title, Scene.title, chapters.c.title),
            case((scripts, "script"), (is_scene, "scene"), else_="chapter"),
            task.target_id,
            case(
                (scripts, Script.id.is_not(None) & Script.deleted_at.is_(None)),
                (is_scene, Scene.id.is_not(None) & Scene.deleted_at.is_(None)),
                else_=func.coalesce(chapters.c.available, False),
            ),
            literal("故事与分镜"),
            task.operation,
            status=status,
            scene_progress=summary,
        )
        .select_from(task)
        .outerjoin(Script, and_(scripts, Script.project_id == pid, Script.id == task.target_id))
        .outerjoin(Scene, and_(is_scene, Scene.project_id == pid, Scene.id == task.target_id))
        .outerjoin(
            chapters, and_(task.operation == "chapter_decompose", chapters.c.id == task.target_id)
        )
        .where(task.project_id == pid, task.operation.in_(allowed))
    )
    adapted = (
        projection(
            adaptation,
            "adaptation",
            func.coalesce(chapters.c.title, adaptation.title),
            "chapter",
            adaptation.chapter_id,
            func.coalesce(chapters.c.available, False),
            literal("故事与分镜"),
            literal("chapter_to_script"),
        )
        .select_from(adaptation)
        .outerjoin(chapters, chapters.c.id == adaptation.chapter_id)
        .where(adaptation.project_id == pid, can(ctx.role, "narrative.edit"))
    )
    return {"creative": creative, "adaptation": adapted}


def fill_detail(db, ctx, task, result):
    root = f"/projects/{ctx.project.id}"
    row = result["summary"]
    available = row["target_available"]
    if available:
        if row["target_type"] == "chapter":
            chapter = db.get(Chapter, row["target_id"])
            path = f"/narrative?novel={chapter.novel_id}&chapter={chapter.id}"
            label = "查看原章节"
        elif row["target_type"] == "scene":
            path, label = f"/storyboard?scene={task.target_id}", "查看场次分镜"
        else:
            path, label = f"/scripts/{task.target_id}", "查看剧本"
            if task.operation == "script_decompose":
                path += "?tool=scenes"
        result["source"] = {"label": label, "path": root + path}

    if isinstance(task, ScriptGenerationTask):
        if task.status == "succeeded" and task.script_id:
            script = db.get(Script, task.script_id)
            if script and script.project_id == ctx.project.id and not script.deleted_at:
                result["source"] = {
                    "label": "打开生成的剧本",
                    "path": f"{root}/scripts/{script.id}",
                }
        return
    if task.operation == "scene_breakdown":
        shots = (task.result or {}).get("output")
        if isinstance(shots, list):
            result["scenes"] = []
            for index, shot in enumerate(shots, 1):
                error = (shot.get("storyboard") or {}).get("breakdown_error")
                result["scenes"].append(
                    {
                        "title": shot.get("title") or f"镜头 {index}",
                        "state": "failed" if error else "succeeded",
                        "shots": 1,
                        "error": error,
                    }
                )
        return
    if task.operation not in scene_progress.OPERATIONS:
        return
    scenes = ((task.result or {}).get("output") or {}).get("scenes", [])
    result["scenes"] = [
        {
            "title": s["title"],
            "state": scene_progress.scene_state(s),
            "shots": len(s.get("shots") or []),
            "error": s.get("error") if scene_progress.scene_state(s) == "failed" else None,
        }
        for s in scenes
    ]
    unfinished = any(s["state"] != "succeeded" for s in result["scenes"])
    if (
        available
        and scenes
        and task.status in ("failed", "succeeded", "applied")
        and (unfinished or task.status == "failed")
    ):
        from app.modules.narrative.tool_tasks import latest

        if latest(db, ctx.project.id, task.operation, task.target_id).id != task.id:
            return
        result["actions"].append(
            {
                "name": "retry",
                "label": "只重试未完成场次" if unfinished else "恢复已完成结果",
                "path": f"{root}/creative-tool-tasks/{task.id}/retry-scenes",
                "payload": None,
                "note": "",
            }
        )
