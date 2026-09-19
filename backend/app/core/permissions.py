"""权限矩阵(对照 04-接口契约 第 2 章)。

写操作按 (action -> 允许角色集合) 校验;读操作默认对项目任意成员开放。
"""
from app.core.errors import Forbidden
from app.models.enums import Role

# 写/敏感动作 → 允许的角色
PERMISSIONS: dict[str, set[Role]] = {
    "project.manage": {Role.admin},
    "director.edit": {Role.admin, Role.director, Role.artist},
    "member.manage": {Role.admin},
    "quota.manage": {Role.admin},
    "provider.manage": {Role.admin},
    "narrative.edit": {Role.admin, Role.director, Role.writer},
    "asset.edit": {Role.admin, Role.director, Role.artist},
    "prompt.edit": {Role.admin, Role.director, Role.artist},
    "shot.edit": {Role.admin, Role.director, Role.artist},
    "generation.trigger": {Role.admin, Role.director, Role.artist},
    "review.submit": {Role.admin, Role.director, Role.artist},
    "review.decide": {Role.admin, Role.director},
    "lock.baseline": {Role.admin, Role.director},
    "skill.edit": {Role.admin,Role.director,Role.artist,Role.writer},
    "agent.run": {Role.admin, Role.director, Role.artist, Role.writer},
    "canvas.edit": {Role.admin, Role.director, Role.artist, Role.writer},
    "timeline.edit": {Role.admin, Role.director},
}


def can(role: Role, action: str) -> bool:
    allowed = PERMISSIONS.get(action)
    if allowed is None:
        # 未登记的动作视为只读,任意成员可执行
        return True
    return role in allowed


def require(role: Role, action: str) -> None:
    if not can(role, action):
        raise Forbidden(f"角色 {role.value} 无权执行 {action}", {"action": action, "role": role.value})
