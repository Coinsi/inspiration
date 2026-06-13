# Inspiration 后端

> 影视 / 动漫 AIGC 资产管理与生产平台的后端:**FastAPI + PostgreSQL(pgvector)+ SQLAlchemy + Alembic**,模块化单体架构。

贯通 **小说 → 设定 → 剧本 → 场次 → 镜头 → 资产 → 生成 → 评审 → 时间线 → 成片** 全流程,以"资产管理 + 配置管理(版本/基线)"为内核。

---

## 一、技术栈

| 关注点 | 选型 |
|---|---|
| Web 框架 | FastAPI(同步路由,线程池执行;阻塞式 I/O 不冻结事件循环) |
| ORM / 迁移 | SQLAlchemy 2.x + Alembic |
| 数据库 | PostgreSQL 16;相似检索预留 pgvector(可降级关闭) |
| 任务 | Celery + Redis(本地以 `CELERY_EAGER=true` 同步执行,免 worker) |
| 对象存储 | MinIO(S3 兼容)/ 本地文件系统(`STORAGE_BACKEND=fs`)双后端,内容寻址(CAS) |
| 鉴权 | JWT;项目级 RBAC(角色 → 动作权限矩阵) |
| 凭据安全 | 供应商密钥用 Fernet 加密入库 |

## 二、架构总览

```
app/
├─ main.py            # FastAPI 入口,挂载各模块路由
├─ core/              # 配置 / 数据库 / 安全 / 权限矩阵 / 依赖 / 错误 / 审计 / 加密
├─ models/            # SQLAlchemy 模型(按限界上下文拆文件)
├─ modules/           # 业务模块(每个含 router.py / service.py / schemas.py)
├─ adapters/          # 可插拔策略:契约(contracts.py)+ 各供应商实现
├─ kernel/            # 通用内核:versioning(版本快照)/ graph(依赖图)
├─ registry/          # 策略注册中心
├─ storage/           # 内容寻址存储(CAS)
├─ platform/          # 平台能力(编码序号 coding 等)
└─ tasks/             # Celery 任务
alembic/              # 数据库迁移
```

### 两条核心设计主线

1. **可插拔策略(开闭原则)**:所有易变的外部能力都抽象为「契约 + 注册中心 + 多实现」,见 `adapters/contracts.py`:
   - `GenerationProvider` 生成供应商:`mock` / `jimeng`(图+视频)/ `gpt_image`(仅图,流式)
   - `DecompositionStrategy` 内容拆解/改编/设定提取:`mock` / `cloud_llm`(OpenAI 兼容)
   - `AssistEngine` AI 对话助手引擎:`mock` / `cloud_llm`
   - `NovelParser` 小说解析:txt / markdown
   - `ConsistencyStrategy` 一致性:参考图 / LoRA / 提示词片段
2. **多版本配置管理**:`kernel/versioning.py` 提供通用版本快照(多态 `version` 表,`entity_type + entity_id + version_no`),被 asset / script / scene / shot / setting 等复用;支持快照、diff、回滚、锁定、基线。

## 三、业务模块(`app/modules/`)

| 模块 | 职责 |
|---|---|
| `identity` | 登录、用户、项目、成员授权、审计 |
| `narrative` | 小说导入/章节、**剧本正文(分块 typed blocks)**、场次、AI 改编与拆解、Fountain 导出 |
| `setting` | **故事圣经(设定库)**:AI 通读小说提取设定(波次并发 + 增量合并)、转资产、设定上下文注入 |
| `asset` | 资产库 CRUD + 版本 + 参考图(CAS)+ 回收站 + 批量删 + 章节维度统计 |
| `consistency` | 视觉身份与一致性策略绑定 |
| `prompt` | 提示词 / 片段库 / BOM 组合 |
| `shot` | 镜头、分镜规格(storyboard)、镜头↔资产引用、提示词组合 |
| `generation` | 生成编排、供应商/配额配置、成本、不可变溯源、变体钦定、参考图物化 |
| `assist` | **贯穿式 AI 对话助手**:会话 + 对象适配器(script/scene/shot/asset/setting)+ 结构化 ops + 预览 + 应用(版本快照) |
| `review` | 评审 / 批注 / 返修 / 镜头状态联动 |
| `timeline` | 时间线组装、成片基线冻结、基线对比 |
| `media` | 鉴权媒体读取(`/blobs/{hash}`) |

## 四、几条关键链路

- **小说 → 剧本**:`narrative.generate_script_from_chapter` → 改编策略产出分块剧本,**自动注入该章设定上下文**(故事圣经,保证人设/世界观一致)。
- **剧本 → 场次/镜头 + 人物/场景资产**:`decompose_script` 按剧本场景头切分;`extract_entities` 抽实体落资产。
- **设定库全书提取**:`setting.start_extraction` 起后台线程,**波次并发**逐章提取,波内同名设定由 LLM 融合,可中断续跑、可按章节范围。
- **AI 对话助手改稿**:`assist.post_message` → 引擎产出 ops → dry-run 预览 → `apply_message` 原子应用 + 版本快照(可回退),按对象类型校验写权限。
- **生成出图**:`generation.submit` 组合提示词 + 收集参考图 → `run_job` 物化参考图为 base64 → 供应商出图 → CAS 落库 + 配额扣减 + 溯源。

## 五、本地运行

### 方式 A:守护脚本(推荐,自愈不掉线)

```powershell
# 在 backend 目录:首次需先建表 + 种子(见下),之后用计划任务托管守护
./run-daemon.ps1            # uvicorn 退出自动 2s 重启;事件日志 backend.log、进程输出 uvicorn.log
```

> 实践中由 Windows 计划任务托管 `run-daemon.ps1`,使服务脱离终端会话、崩溃自愈。
> 普通前台启动用 `./run.ps1`(带 `--reload`,改代码自动生效;本机 reload 偶有不可靠)。

### 方式 B:标准命令

```bash
cp .env.example .env          # 按需改键值(本地 host 用 localhost)
pip install -e ".[dev]"
alembic upgrade head          # 建/迁移全表
python -m app.seed            # 种子数据(demo / demo1234)
uvicorn app.main:app --reload --port 8000
# 文档:http://127.0.0.1:8000/docs   (注意用 127.0.0.1,勿用 localhost 以免走 IPv6)
```

### 方式 C:Docker(一体化,见根目录 `deploy/`)

```bash
cd ../deploy && docker compose up --build   # 自动迁移 + 种子;前端:5173 / API:8000 / MinIO:9001
```

## 六、环境配置(`.env`,不入库)

`.env.example` 为模板。关键项:

- `SECRET_KEY` JWT 签名密钥(生产务必改)
- `DATABASE_URL` 本地 PostgreSQL(开发默认 `postgres / 123456`)
- `STORAGE_BACKEND` `fs`(本地文件系统,无需 MinIO)/ `minio`
- `CELERY_EAGER=true` 任务同步执行(本地免 worker)
- `CREDENTIALS_FERNET_KEY` 供应商密钥加密钥(留空自动派生)
- `ENABLE_PGVECTOR` 未装 vector 扩展时设 `false` 自动降级
- 云供应商密钥**不放 .env**:LLM / 即梦 / GPT Image 的端点与 Key 在「项目设置」里配置,加密入库

## 七、数据库迁移

| 版本 | 内容 |
|---|---|
| `0001_initial` | 全表(34 表) |
| `0002_shot_storyboard` | 镜头分镜规格 JSONB |
| `0003_script_content_blocks` | 剧本正文分块 + 溯源章节 |
| `0004_assist_chat` | AI 对话助手会话 |
| `0005_setting` | 设定库 + 全书提取任务 |

```bash
alembic upgrade head          # 应用到最新
alembic revision -m "xxx"     # 新建迁移(本项目迁移为手写,非 autogenerate)
```

## 八、生成供应商对接

| 供应商 | 模态 | 说明 |
|---|---|---|
| `mock` | 图/视频 | 离线占位,打通链路与验收 |
| `jimeng` | 图/视频 | 即梦/火山引擎(请求体/签名按其 API 对接,部分为 TODO 桩) |
| `gpt_image` | 仅图 | OpenAI 兼容 Images API,**流式生成**规避网关超时;`/images/edits` 走参考图;质量可配 |

参考图统一由服务层 `_materialize_references` 物化为 base64 data URI(fs/MinIO 通用)后下发,契约层只传 `blob_hash`。
