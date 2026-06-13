#!/usr/bin/env bash
set -e

echo "==> 等待数据库并执行迁移(建全表)"
alembic upgrade head

echo "==> 写入开发种子数据(demo / demo1234)"
python -m app.seed || true

echo "==> 启动 API (uvicorn)"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
