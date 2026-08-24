# Inspiration

> 面向影视、动漫与短片创作的 AIGC 资产管理与生产平台。

Inspiration 尝试把制造业中的**资产管理**与**配置管理**方法引入内容生产：将小说、设定、剧本、场次、镜头、角色、场景、提示词和生成结果都变成可管理、可复用、可追溯的生产资产，贯通从一段文字到成片基线的完整流程。

**FastAPI · React · TypeScript · PostgreSQL/pgvector · Redis/Celery · MinIO · Docker Compose**

> 当前状态：V1 全流程原型已完成，适合本地体验、产品演示和二次开发。默认使用 mock AI/生成策略；接入真实云模型前请先阅读[当前边界](#当前边界)。

## 为什么做 Inspiration

AIGC 降低了单次内容生成的成本，却放大了制作管理的问题：

- 同一个角色在不同镜头里容易“变脸”，风格也难以保持统一；
- 提示词、参考图、模型参数散落在聊天记录和文件夹中，无法可靠复用；
- 上游设定发生变化后，很难知道哪些镜头需要重新生成；
- 生成结果数量快速膨胀，但缺少版本、评审、返修和定版机制；
- 多人协作时，剧本、资产、镜头与成片之间缺少统一的追溯关系。

Inspiration 的目标不是再做一个“AI 生成按钮”，而是为 AI 影视生产建立一套可持续演进的工作流与资产底座。

## 核心构想

### 1. 内容即资产

小说章节、故事设定、剧本块、场次、镜头、角色、道具、场景、提示词和生成结果都拥有结构化身份。资产可以被搜索、引用、复用、软删除和恢复，也可以追踪它被哪些下游内容使用。

### 2. AI 是可插拔的协作者

小说解析、内容拆解、对话辅助、一致性控制和图像/视频生成都通过策略接口接入。核心业务不绑定单一模型或供应商，可以从 mock 策略起步，再按项目切换到云 LLM、GPT Image、即梦或后续新增的适配器。

### 3. 一致性优先于单次生成

角色和场景拥有自己的视觉身份、参考图、LoRA 或提示词片段。镜头生成时通过 BOM 自动组合角色、场景、道具、风格和镜头描述，尽量让同一资产在不同镜头中保持一致。

### 4. 每个结果都可追溯

生成任务在提交时冻结提示词、模型、参数和参考图版本；文本资产保留修订历史，镜头引用支持浮动或钉死版本。成片基线可以冻结一整套制作配置，使历史方案能够复现、比较和回退。

### 5. 人始终掌握最终决定权

AI 负责提出拆解建议、资产草稿、修改方案和生成变体，人负责确认、评审、返修、钦定代表版本与冻结最终基线。

## 从小说到成片

```mermaid
flowchart LR
    A[导入小说] --> B[AI 通读与故事圣经]
    B --> C[剧本改编]
    C --> D[场次与镜头拆解]
    D --> E[角色 / 场景 / 道具资产]
    E --> F[提示词 BOM 与一致性]
    F --> G[图像 / 视频生成]
    G --> H[评审与返修]
    H --> I[时间线组装]
    I --> J[成片基线]
```

任一环节都可以向前查看来源、向后分析影响：例如角色设定更新后，系统可以定位所有引用它的镜头，并区分会自动跟随的浮动引用与需要人工升级的钉死引用。

## 界面预览

### 一站式项目工作台

从小说和章节出发，集中查看脚本、角色、分镜、场景和成片状态。

![Inspiration 项目工作台](docs/screenshots/workbench.png)

### 故事圣经

让 AI 通读小说并持续沉淀人物、世界观、力量体系、势力、地点、道具、术语和大事记，为后续改编与生成提供统一上下文。

![Inspiration 故事圣经](docs/screenshots/story-bible.png)

### 资产库

统一管理角色、道具、场景、服化道、载具、风格与模型资产，支持卡片、图谱和列表视图，以及版本、参考图、生成变体和回收站。

![Inspiration 资产库](docs/screenshots/asset-library.png)

### 分镜工作台

按场景编排镜头，管理景别、机位、运镜、时长、转场、画幅、节奏、对白、音效与导演提示。

![Inspiration 分镜工作台](docs/screenshots/storyboard.png)

## 主要能力

| 领域 | 能力 |
|---|---|
| 内容创作 | 小说导入、章节管理、分块剧本编辑、Fountain 导出、AI 改编与场次/镜头拆解 |
| 故事圣经 | 全书分批通读、增量提取与融合、章节范围筛选、设定转资产 |
| 资产管理 | 角色/道具/场景等资产 CRUD、编码、标签、参考图、版本、影响分析、软删除与恢复 |
| 提示词与一致性 | 提示词片段库、BOM 自动组合、参考图/LoRA/提示词三种一致性策略 |
| 镜头生产 | 分镜规格、资产引用、制作状态机、进度看板、浮动/钉死版本 |
| 生成编排 | 图像/视频统一任务模型、配额与成本、不可变溯源、变体评分与代表版钦定 |
| 评审与成片 | 画面批注、视频时间码评论、返修轮次、时间线、粗剪/精剪/定剪基线 |
| 协作与安全 | JWT 登录、项目级 RBAC、成员角色、关键操作权限、审计日志、供应商密钥加密 |

## 快速开始

### 方式一：Docker Compose 一键启动（推荐）

准备环境：

- Git
- Docker Desktop，或安装了 Docker Compose 的 Docker Engine

```bash
git clone https://github.com/Coinsi/inspiration.git
cd inspiration/deploy
docker compose up --build -d
```

首次启动需要下载并构建镜像，完成后访问：

| 服务 | 地址 | 说明 |
|---|---|---|
| Web | <http://127.0.0.1:5173> | Inspiration 前端 |
| API 文档 | <http://127.0.0.1:8000/docs> | FastAPI Swagger UI |
| MinIO 控制台 | <http://127.0.0.1:9001> | 默认 `minioadmin / minioadmin` |

演示账号：

```text
用户名：demo
密码：demo1234
```

查看服务状态与日志：

```bash
docker compose ps
docker compose logs -f api web worker
```

停止服务：

```bash
docker compose down
```

数据库和对象文件保存在 Docker volumes 中，普通 `down` 不会删除数据。需要全新初始化时可使用 `docker compose down -v`，但这会永久删除当前 Docker 数据卷。

### 方式二：本地开发

本地开发需要 Python 3.11+、Node.js 20+、PostgreSQL，以及按需启用的 Redis/MinIO。普通 PostgreSQL 未安装 pgvector 时，请在 `backend/.env` 中设置 `ENABLE_PGVECTOR=false`。

后端（PowerShell）：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

将 `.env` 中 Docker 服务名改为本机地址，例如将 `postgres`、`redis`、`minio` 分别改为 `localhost`，然后执行：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.seed
.\run.ps1
```

前端另开一个终端：

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

开发模式入口仍为 <http://127.0.0.1:5173>。项目也提供 `backend/run-daemon.ps1` 与 `frontend/run-daemon.ps1`，用于服务退出后自动重启。

## 五分钟体验路径

1. 使用 `demo / demo1234` 登录并进入“示例项目”。
2. 在“拆解工作台”导入 `samples/示例小说.txt`，查看章节并生成剧本或拆解建议。
3. 在“设定”中查看故事圣经，体验按分类、章节和关键字筛选，以及“转为资产”。
4. 在“资产库”查看角色、场景、道具、版本、引用关系和生成变体。
5. 在“镜头看板”和“分镜”中查看制作状态与镜头规格。
6. 在“设置”中配置真实 LLM 或生成供应商；不配置时可使用 mock 策略体验完整业务链路。
7. 将定版镜头放入时间线，并在“成片 / 基线”中冻结与比较方案。

## 技术架构

```text
React + TypeScript + Vite
          │
          │ HTTP / JWT
          ▼
FastAPI 模块化单体
  ├─ identity / narrative / setting / asset
  ├─ prompt / shot / generation / review / timeline
  ├─ versioning / graph / permissions / audit
  └─ adapters: parser / LLM / generation / consistency
          │
          ├─ PostgreSQL + pgvector   结构化数据与向量预留
          ├─ Redis + Celery          异步任务与生成编排
          └─ MinIO / 本地文件系统    CAS 内容寻址存储
```

项目采用模块化单体：保持单机部署简单，同时按业务上下文隔离模块，并通过策略与注册中心为未来接入新模型、供应商和存储后端保留扩展点。

## 仓库结构

```text
inspiration/
├─ backend/      FastAPI、SQLAlchemy、Alembic、Celery 与业务模块
├─ frontend/     React、TypeScript、Tailwind 与业务页面
├─ deploy/       Docker Compose 一体化部署
├─ docs/         需求、概要设计、数据模型、接口契约与开发计划
├─ samples/      可用于体验导入流程的示例文本
└─ README.md
```

更详细的设计资料：

- [文档索引](docs/README.md)
- [需求规格说明书](docs/01-需求规格说明书.md)
- [概要设计说明书](docs/02-概要设计说明书.md)
- [数据模型](docs/03-详细设计-数据模型.md)
- [接口契约](docs/04-详细设计-接口契约.md)
- [开发计划与里程碑](docs/05-开发计划与里程碑.md)

## 当前边界

- 项目当前定位为 V1 原型与二次开发底座，尚未针对公网生产环境完成完整加固。
- 默认 mock 策略无需外部 API 即可演示流程；真实 AI 拆解与生成需要在项目设置中配置供应商地址、模型与密钥。
- GPT Image 已提供适配器；即梦适配器的请求体、签名和任务轮询仍需要按照实际使用的火山引擎/即梦 API 完成对接。
- 相似图检索、专业 NLE 导出、音轨/配音/字幕、实时协作、C2PA 水印等目前属于预留扩展点。
- 正式部署前必须替换 `SECRET_KEY`、MinIO 默认凭据和其他示例配置，并根据部署域名收紧 CORS 与网络暴露范围。

## 项目愿景

我们希望 Inspiration 最终不只是“生成内容的工具”，而是一个面向 AI 原生影视生产的配置管理系统：每一个角色都有稳定身份，每一个镜头都知道自己来自哪里，每一次生成都可以复现，每一版成片都能够回到当时完整的创作上下文。

当模型能力不断变化时，真正长期有价值的不是某一次生成，而是围绕作品积累下来的资产、关系、版本、决策与生产方法。
