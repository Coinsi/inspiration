"""Resolve a reply to one versioned run without granting broader authority."""

import re
import uuid

from app.core.errors import Conflict
from app.modules.harness import guidance, schemas, service


def respond(db, ctx, run, key, content, expected_revision):
    if run.revision != expected_revision:
        raise Conflict("制作进展已更新，请查看最新消息后再发送；你的输入已保留")
    phrase = re.sub(r"[\s，。！？,.!?]", "", content).lower()
    yes = phrase in {
        "可以",
        "好的",
        "好",
        "确认",
        "同意",
        "开始吧",
        "继续吧",
        "按这个来",
        "可以开始",
        "yes",
        "ok",
        "确认方案",
        "采用",
        "采用这次修改",
    }
    no = phrase in {"不同意", "不采用", "不要修改", "拒绝", "no"}
    rows = [t for t in service.tasks(db, run) if t.status != "superseded"]
    pending = [
        t
        for t in rows
        if t.status in {"waiting_input", "waiting_review", "waiting_candidate", "waiting_plan"}
    ]

    def control(action, targets=(), **extra):
        service.control(
            db,
            ctx,
            run.id,
            schemas.Control(
                request_key=key,
                expected_revision=run.revision,
                action=action,
                task_ids=[t.id for t in targets],
                message=content,
                **extra,
            ),
        )

    # These commands are explicit and do not depend on interpreting "yes".
    if phrase in {"暂停", "暂停一下"} and run.status not in {"paused", "succeeded", "canceled"}:
        control("pause")
        return None
    if phrase in {"停止", "停止任务", "取消任务"} and run.status not in {"succeeded", "canceled"}:
        control("cancel")
        return None
    if phrase in {"继续", "恢复执行", "继续执行"} and run.status == "paused":
        control("resume")
        return None
    if (
        yes
        and len(pending) <= 1
        and run.status == "needs_plan_approval"
        and not guidance.pending(run)
    ):
        # Preserve the already displayed budget; a short confirmation never raises it.
        control("approve_plan")
        return None
    if len(pending) == 1:
        task = pending[0]
        confirmation = task.state.get("guidance_confirmation")
        if yes and confirmation and guidance.pending(run) == confirmation["guidance_id"]:
            guidance.confirm(
                db, ctx, run.id, uuid.UUID(confirmation["guidance_id"]), key, expected_revision
            )
            return confirmation["guidance_id"]
        if not guidance.pending(run) and task.role != "impact":
            if task.status == "waiting_input":
                control("reply", [task])
                return None
            if task.status == "waiting_review" and (yes or no):
                review = task.state.get("review", {}).get("review", {})
                if review.get("tool") == "object.propose_edit" and task.state.get("review_id"):
                    control(
                        "approve_tool" if yes else "reject_tool",
                        [task],
                        review_id=uuid.UUID(task.state["review_id"]),
                    )
                    return None
    # Ambiguous decisions and new instructions stay in the read-only impact flow.
    return guidance.receive(db, ctx, run.id, key, content)["guidance_id"]
