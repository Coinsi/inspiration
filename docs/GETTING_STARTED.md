# 安装与本地体验

[返回项目首页](../README.md) · [文档导航](README.md)

## Windows 一键启动

需要 Python 3.11+、Node.js 20+。没有已配置的 PostgreSQL、MinIO 等服务时，需要 Docker Desktop。首次安装依赖和拉取镜像需要联网。

克隆仓库后，在根目录双击 `启动项目.bat`，或运行：

```powershell
python start.py
```

不打开浏览器、不等待按键：

```powershell
python start.py --no-browser --no-pause
```

启动器沿用 `backend/.env`，只在配置文件不存在时从示例创建；复用已启动服务，不重置已有账号、项目和数据卷。数据库版本不匹配时会提示先停止旧后端，再执行迁移并启动。自定义数据库无法连接时不会自动切换到别的数据库。

服务在后台运行，关闭启动窗口不会停止服务。日志位于根目录 `.runtime/`；修改后端代码后需要重启对应进程。

## Docker Compose

需要 Git，以及 Docker Desktop 或带 Compose 的 Docker Engine。

```bash
git clone https://github.com/Coinsi/inspiration.git
cd inspiration/deploy
docker compose up --build -d
```

| 服务 | 本地地址 |
| --- | --- |
| 产品首页 | <http://127.0.0.1:5173> |
| 登录 / 注册 | <http://127.0.0.1:5173/login> / <http://127.0.0.1:5173/register> |
| 博客 | <http://127.0.0.1:5173/blog> |
| API 文档 | <http://127.0.0.1:8000/docs> |
| MinIO 控制台 | <http://127.0.0.1:9001> |

开发演示账号为 `demo / demo1234`，MinIO 示例凭据为 `minioadmin / minioadmin`。已有同名账号不会重置密码。生产环境不创建演示账号；开发默认凭据不能用于公网部署。

```bash
docker compose ps
docker compose logs -f api web worker
docker compose down
```

普通 `down` 保留数据卷。不要为了排错随意加 `-v`：它会删除数据库和对象存储数据卷。

## 分开运行前后端

需要 PostgreSQL，以及按配置启用的 Redis / MinIO。普通 PostgreSQL 没有 pgvector 扩展时，在 `backend/.env` 设置 `ENABLE_PGVECTOR=false`。

后端（PowerShell）：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

编辑 `.env`，将 Docker 服务名 `postgres`、`redis`、`minio` 改为实际服务地址（例如 `localhost`），核对数据库凭据后运行：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.seed
.\run.ps1
```

默认配置使用 Celery，另开终端运行 Worker：

```powershell
cd backend
.\.venv\Scripts\python.exe -m celery -A app.tasks.celery_app.celery_app worker -l info --pool=solo
```

前端另开终端：

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

## 第一次体验

1. 登录并进入示例项目。新安装只有空项目，README 截图中的创作内容不会自动导入。
2. 在“故事与剧本”导入 [示例小说](../samples/示例小说.txt)，整理章节、设定与剧本。
3. 在素材库新建角色或场景，上传自己的参考图；在自由画布连接提示词与参考。
4. 用 Mock 策略体验任务提交、状态跟踪和结果审阅。真实模型需配置渠道、模型与凭据，兼容协议仍需实测。
5. 有了有效媒体片段后，进入剪辑工作区编排并导出。FFmpeg 等媒体依赖见[后端说明](../backend/README.md)。

## 按需配置

| 能力 | 说明 |
| --- | --- |
| 模型渠道 | [支持的协议、参数与接入边界](46-渠道与模型接入改造验收.md) |
| 视频语义检索 | [独立视觉索引服务](../services/media-indexer/README.md)；需准备模型 |
| 自动转写 | [独立转写服务](../services/transcriber/README.md)；需准备模型 |
| 外部 AI / MCP | [网关配置](../services/mcp-gateway/README.md) |
| 官网管理 | [平台管理员授权与发布流程](61-产品官网与内容管理.md)；demo 默认不是平台管理员 |
| 博客 | [文章管理、初始内容与发布](62-博客与创作手记.md) |

## 遇到问题

- **页面能开、操作报错：** 查看 API 和 Worker 日志，确认数据库迁移与代码版本一致。
- **任务持续等待：** 检查 Worker / Redis，或当前选择的任务执行方式是否正确配置。
- **素材无法播放：** 检查存储服务与文件是否存在。只备份数据库不能保全原始媒体。
- **管理端拒绝访问：** 项目管理员与平台管理员是不同权限，不能通过前端菜单绕过授权。

健康检查、备份及公网部署配置见[运行可靠性与部署说明](59-运行可靠性与部署修复.md)。
