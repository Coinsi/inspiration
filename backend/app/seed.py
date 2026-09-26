"""开发种子数据:创建一个 demo 用户 + demo 项目。

用法(容器内或本地):python -m app.seed
"""
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.enums import Role
from app.models.identity import Membership, Project, User


def run() -> None:
    from app.core.config import settings
    if not settings.is_dev:
        print("生产环境不创建演示账号。")
        return
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.username == "demo"))
        if user is None:
            user = User(
                username="demo",
                email="demo@example.com",
                password_hash=hash_password("demo1234"),
                display_name="Demo 用户",
            )
            db.add(user)
            db.flush()
            print("✓ 创建用户 demo / demo1234")

        project = db.scalar(select(Project).where(Project.code == "DEMO"))
        if project is None:
            project = Project(code="DEMO", name="示例项目", owner_id=user.id, settings={})
            db.add(project)
            db.flush()
            db.add(Membership(project_id=project.id, user_id=user.id, role=Role.admin))
            print("✓ 创建项目 DEMO(demo 为管理员)")

        db.commit()
        print("种子数据完成。")
    finally:
        db.close()


if __name__ == "__main__":
    run()
