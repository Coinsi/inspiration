# Inspiration 后端

FastAPI + PostgreSQL(pgvector) + Celery/Redis + MinIO 的模块化单体。

## 目录结构

```
app/
├─ main.py            # FastAPI 入口
├─ core/              # 配置、数据库、安全、权限网关、依赖、错误、审计
├─ models/            # 全部 SQLAlchemy 模型(按模块拆文件,M0 一次性建全表)
├─ modules/           # 限界上下文(router/service/schemas);M0 已实现 identity
├─ adapters/          # 可插拔策略契约与实现(contracts.py + 各供应商)
├─ registry/          # 策略注册中心
├─ storage/           # 内容寻址存储(CAS, MinIO)
├─ platform/          # 编码序号等平台能力
├─ tasks/             # Celery 任务
└─ seed.py            # 开发种子数据
alembic/              # 数据库迁移(0001 建全表)
```

## 本地运行(推荐用根目录 docker-compose)

```bash
# 1) 准备依赖与环境
cp .env.example .env
pip install -e ".[dev]"

# 2) 起 Postgres/Redis/MinIO 后,建表
alembic upgrade head

# 3) 种子数据(demo / demo1234)
python -m app.seed

# 4) 启动 API
uvicorn app.main:app --reload --port 8000
# 文档:http://localhost:8000/docs

# 5)(可选)启动 Celery worker
celery -A app.tasks.celery_app.celery_app worker -l info
```

## 本地一键启动(无 Docker · 推荐)

已提供 `run.ps1` + `.env`(均在 `.gitignore` 中,不入库):

```powershell
# backend 目录下
./run.ps1                 # 默认 127.0.0.1:8000,开启 --reload(改代码自动生效)
./run.ps1 0.0.0.0 8000    # 可选:自定义 bind/port
```

- `.env` 固定了 `SECRET_KEY`(重启后端不再使已登录会话失效)、本地数据库(`postgres/123456`)、
  `STORAGE_BACKEND=fs`(本地文件系统,无需 MinIO)、`CELERY_EAGER=true`(任务同步执行,无需 worker)。
- 首次使用前仍需:`alembic upgrade head` 建表 + `python -m app.seed` 写种子。
- 复制模板:本地无 `.env` 时可 `cp .env.example .env` 再按上面键值改(注意 host 改 `localhost`)。

> ⚠️ `run.ps1` 为纯 ASCII(Windows PowerShell 5.1 按系统码页读取脚本,中文易乱码)。

## 连接普通 Postgres(无 pgvector)

pgvector 是 EXT-01 相似检索的预留特性。连接未装该扩展的本地 Postgres 时,设置
`ENABLE_PGVECTOR=false`,系统自动降级:不创建向量列、不依赖 vector 扩展。
docker-compose 使用 `pgvector/pgvector` 镜像,保持默认 `true` 即可。

**想在本地 PostgreSQL 上启用 pgvector(Windows,无官方二进制需源码编译):**

1. 一次性装好「Visual Studio Build Tools」并勾选「使用 C++ 的桌面开发」工作负载:
   <https://visualstudio.microsoft.com/visual-cpp-build-tools/>
2. 运行(脚本会自动提权、克隆编译 pgvector、装入 PG、并对库执行启用 SQL):

   ```powershell
   ./scripts/install-pgvector.ps1
   ```
3. 把 `.env` 的 `ENABLE_PGVECTOR` 改回 `true`,重启后端(`run.ps1`)。

> 仅做 DB 侧启用(二进制已就位时):`psql -U postgres -d inspiration -f scripts/enable-pgvector.sql`
> ——它会 `CREATE EXTENSION vector` 并给 `asset` / `generation` 补 `embedding vector(512)` 列
> (初始迁移在 `ENABLE_PGVECTOR=false` 下建表,这两列当时未创建)。

## M0 验收(已通过)

> 已对真实 Postgres 完成端到端验收:迁移建 34 表,11/11 API 用例通过(含权限 403、未授权 401)。

- `GET /health` 返回 ok
- `POST /api/v1/auth/login`(demo/demo1234)拿到 JWT
- `GET /api/v1/me` 返回用户与项目角色
- `POST /api/v1/projects` 建项目、`/members` 授权、`/audit-logs` 查审计
- 权限:非 admin 调成员/审计接口返回 403
