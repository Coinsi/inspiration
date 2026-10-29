"""Public media stages derived from persisted receipts, without estimated progress."""

from datetime import UTC


def timestamp(value):
    if value is None:
        return None
    return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC).isoformat()


def project(kind, *, job=None, task=None, run=None, completed_outputs=0):
    if kind not in {"image", "video", "audio"}:
        return None
    label = {"image": "图片", "video": "视频", "audio": "音频"}[kind]
    phase = "queued"
    if job is not None:
        if job.status == "failed":
            phase = "failed"
        elif job.status == "canceled":
            phase = "stopped"
        elif job.status == "succeeded":
            phase = "completed" if completed_outputs else "materializing"
        elif (job.routing_state or {}).get("phase") == "generated":
            phase = "materializing"
        elif job.status in {"running", "submitted"}:
            # Some synchronous adapters generate inside submit(). Neither
            # dispatching nor accepted establishes an upstream queue percentage.
            phase = "generating"
    if task is not None and (
        task.status == "validating_job"
        or (task.status == "running" and task.state.get("validation_job_id"))
    ):
        phase = "materializing"
    if task is not None and task.status in {"failed", "uncertain"}:
        phase = "failed"
    elif task is not None and task.status == "canceled":
        phase = "stopped"
    if run is not None and run.status == "canceled":
        phase = "stopped"
    title, message = {
        "queued": ("准备生成", f"已收到要求，正在准备生成{label}。"),
        "generating": (f"正在生成{label}", "正在等待生成服务返回结果，可以离开页面后回来查看。"),
        "materializing": ("正在准备结果", "生成结果已返回，正在接收和检查文件。"),
        "completed": ("结果已就绪", "生成结果已保存，可以预览或继续修改。"),
        "failed": ("生成未完成", "本次生成遇到问题，请查看错误信息后处理。"),
        "stopped": ("已停止后续制作", "已提交的生成可能仍在执行，已有成果会保留。"),
    }[phase]
    sources = [item for item in (run, task, job) if item is not None]
    # started_at is the durable request/task creation time, never a guessed
    # upstream start time; updated_at changes only with persisted state.
    created = [item.created_at for item in sources if item.created_at is not None]
    updated = [timestamp(item.updated_at) for item in sources if item.updated_at is not None]
    result = {
        "kind": kind,
        "phase": phase,
        "title": title,
        "message": message,
        "started_at": min(map(timestamp, created)) if created else None,
        "updated_at": max(updated) if updated else None,
        "completed_outputs": completed_outputs,
    }
    if job is not None:
        result["job_id"] = str(job.id)
    if task is not None:
        result["task_id"] = str(task.id)
    return result
