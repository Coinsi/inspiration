"""Progress in scene units, including receipts written before per-scene status existed."""

OPERATIONS = ("script_decompose", "chapter_decompose")
PENDING_MESSAGE = "尚未完成拆镜"


def scene_state(scene):
    if state := scene.get("breakdown_status"):
        return state
    if scene.get("error") and scene["error"] != PENDING_MESSAGE:
        return "failed"
    return "succeeded" if scene.get("shots") else "pending"


def progress(scenes, *, attempt=1, preserved=0):
    states = [scene_state(scene) for scene in scenes]
    current = next((i for i, state in enumerate(states) if state == "running"), None)
    return {
        "total": len(scenes),
        "completed": states.count("succeeded"),
        "failed": states.count("failed"),
        "shots": sum(
            len(scene.get("shots") or []) for scene in scenes if scene_state(scene) == "succeeded"
        ),
        "current": current + 1 if current is not None else None,
        "current_title": scenes[current]["title"] if current is not None else None,
        "attempt": attempt,
        "preserved": preserved,
    }


def with_progress(result, output):
    previous = (result or {}).get("progress") or {}
    return {
        **(result or {}),
        "output": output,
        "progress": progress(
            output["scenes"],
            attempt=previous.get("attempt", 1),
            preserved=previous.get("preserved", 0),
        ),
    }


def queue_unfinished(task):
    output = task.result["output"]
    scenes = [
        dict(scene, breakdown_status="pending", error=PENDING_MESSAGE)
        if scene_state(scene) != "succeeded"
        else dict(scene, breakdown_status="succeeded")
        for scene in output["scenes"]
    ]
    preserved = sum(scene_state(scene) == "succeeded" for scene in scenes)
    attempt = (task.result.get("progress") or {}).get("attempt", 1) + 1
    task.result = {
        **task.result,
        "output": {**output, "scenes": scenes},
        "progress": progress(scenes, attempt=attempt, preserved=preserved),
    }
    task.status, task.error = "queued", None
