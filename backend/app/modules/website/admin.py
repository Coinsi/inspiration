"""Explicit server-side administrator provisioning; never inferred from project roles."""

import argparse

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.identity import User
from app.modules.admin.accounts import AccountChange, change


def main():
    parser = argparse.ArgumentParser(description="配置独立的平台管理员身份")
    parser.add_argument("action", choices=["grant", "revoke"])
    parser.add_argument("username")
    args = parser.parse_args()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == args.username))
        if user is None or not user.is_active:
            parser.error("已启用的注册用户不存在")
        change(
            db,
            None,
            user.id,
            AccountChange(
                expected_revision=user.admin_revision,
                action="set_role",
                role="super" if args.action == "grant" else "user",
                reason="服务端管理员授权命令",
            ),
            bootstrap=True,
        )
        db.commit()
    print(f"{args.action}: {args.username}")


if __name__ == "__main__":
    main()
