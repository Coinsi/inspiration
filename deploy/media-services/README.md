# 素材处理服务（集成施工中）

本目录提供独立 MediaCMS 服务的配置和上游版本清单。配置可解析、镜像能构建、HTTP 健康、视频能转码、产品端到端可用是不同验收层级，不能相互替代。WISE 桥接实现位于 `services/wise-bridge`；本目录不把旧有 embedding 服务冒充 WISE。

## 固定版本和边界

- MediaCMS `v9.1.0` / `1ab99af4587bfc515582783c9ee19ae12e0fd065`，使用官方 Dockerfile 的 `base` 目标加 CPU PyTorch 与相同版本 Whisper，默认不开转写，避免启动即下载模型。上游 `full` 实测会额外安装数 GB CUDA 库，本机 CPU 验证镜像不需要这些库。
- WISE `fcfa443fbb46eb361bb19151339338616838a5b5`，上游包版本 `2.1.0+dev`，没有可用的 GitHub release。桥接必须验证对应源码版本。
- 应用源码固定不等于构建字节完全可复现：上游 Dockerfile 仍读取基础镜像标签、系统软件仓库和部分可变下载地址。真实构建后应记录镜像 ID/digest，模型另记 revision/checksum。
- inspiration 管理用户/项目权限、素材 ID、不可变版本、引用与删除策略；MediaCMS 管理它自己的媒体记录、编码任务和派生文件；WISE 管理检索项目与特征。外部 ID 只能作为映射，不能成为产品权限依据。
- 不改现有 `deploy/docker-compose.yml`，不复用现有数据库、Redis 或媒体卷。

## 配置与启动

把 `.env.example` 复制为本目录 `.env`，生成各自独立的随机密钥。该文件被项目现有 `.gitignore` 忽略。`MEDIACMS_SERVICE_TOKEN` 必须是 20 随机字节对应的 40 位小写十六进制字符串。不要把实际 `.env` 或展开后的 Compose 内容写入日志、截图或提交。

以下接线步骤适用于 `python start.py` 启动的宿主机后端，媒体服务运行在 Docker 中。主应用的 `deploy/docker-compose.yml` 当前仍读取 `backend/.env.example`，不会自动读取 `backend/.env`。全容器部署需要另行提供私有环境覆盖，并设置 API/worker 能访问的服务网络地址；API 容器内的 `127.0.0.1` 指向容器自身，不能照抄以下宿主机地址。当前默认 Compose 不会自动完成这部分接线。

### 主站与服务配置对应

服务和主站分别读取自己的私有配置，两边需要一致；仅启动容器不会开启主站能力。

| 能力 | `backend/.env`（宿主机后端） | `deploy/media-services/.env`（媒体服务） |
| --- | --- | --- |
| 总入口 | `LIBRARY_PROCESSING_ENABLED=true` | 对应服务完成部署与健康检查 |
| WISE 接线 | `LIBRARY_WISE_URL=http://127.0.0.1:9671`；`LIBRARY_WISE_TOKEN` | 与 `WISE_PORT=9671` 对应；token 与 `WISE_BRIDGE_TOKEN` 相同 |
| 画面、声音检索 | `LIBRARY_WISE_CAPABILITIES=["visual","audio"]` | `WISE_BRIDGE_CAPABILITIES=visual,audio`，并准备和验证视觉、音频模型 |
| MediaCMS 接线 | `LIBRARY_MEDIACMS_URL=http://127.0.0.1:18080`；`LIBRARY_MEDIACMS_TOKEN` | 与 `MEDIACMS_PORT=18080` 对应；token 与 `MEDIACMS_SERVICE_TOKEN` 相同 |
| 自动字幕 | `LIBRARY_MEDIACMS_TRANSCRIPTION_ENABLED=true` | `MEDIACMS_ENABLE_TRANSCRIPTION=true`，并先准备、校验 Whisper 缓存 |
| 自动处理 | `LIBRARY_PROCESSING_AUTO=true`；默认 `LIBRARY_PROCESSING_WISE_AUTO_CAPABILITIES=["visual","audio"]` | 只消费主站实际启用且按素材类型适用的能力 |

主站模板默认关闭总入口、自动处理和字幕，WISE 默认只显示 `visual`。只需要手动处理时可保持 `LIBRARY_PROCESSING_AUTO=false`。启用自动处理还会扫描符合条件的既有素材，不仅处理此后上传的文件；失败和已取消任务仍需手动重试。物体/人脸不在上述基础配置中，须另按可选模型说明部署与验收。

修改主站配置后，先确认队列空闲，再重启使用该配置的 API/worker；修改媒体服务环境时须在空闲后主动应用对应 Compose 配置。`start.py` 会复用已运行服务，不会自动重载已有进程或替换运行中容器。首次部署命令如下。

从项目根目录执行：

```powershell
docker compose -f deploy/media-services/compose.yml --env-file deploy/media-services/.env config --quiet
docker build --target base -t inspiration-mediacms:1ab99af4-base 'https://github.com/mediacms-io/mediacms.git#1ab99af4587bfc515582783c9ee19ae12e0fd065'
docker build -f deploy/media-services/Dockerfile.mediacms -t inspiration-mediacms:1ab99af4-full deploy/media-services
docker compose -f deploy/media-services/compose.yml --env-file deploy/media-services/.env up -d --no-build
docker compose -f deploy/media-services/compose.yml --env-file deploy/media-services/.env ps
```

构建期间会下载 Python/系统依赖（full 镜像体积可观），但不是下载 Whisper/视觉模型权重。使用单独 `docker build` 也避开本次本机 Compose v5 的 bake 启动问题。不要用 `down -v` 作为日常停止方式；它会删除本栈的数据库和素材。

只向宿主回环地址发布 `127.0.0.1:18080`。Postgres、Redis 不发布宿主端口。远程部署应配置认证与 TLS 入口；浏览器继续通过 inspiration，不直接使用 MediaCMS 管理页或原始文件地址。

启动验收还必须检查现有业务 API、数据库、CAS/MinIO 和业务 Redis。新检索/处理容器 healthy 不代表用户能保存素材：2026-10-04 页面实测曾因原业务 MinIO/Redis 停止而在上传时报连接失败，恢复既有容器后才能继续上传。不要只检查本目录两个新栈，也不要为修复该问题删除或新建替代原业务卷。

主 API 已重启应用上传预约与新的索引恢复协议，数据库、存储、队列就绪。最初既有 `generation_worker` 未启动；随后确认主库没有在途生成任务，再启动并交由标准 `start.py` 管理，最新核心 `/health/ready` 返回200。可选旧 `transcriber/indexer` 仍未启动，不能据此把所有可选能力视为已运行；恢复生成 worker 前仍应核对其排队任务，避免恢复状态未知的付费工作。

该服务的 Nginx 明确拒绝 `/media/` 直接下载，所有原始/派生内容必须走鉴权后的桥接。不能把上游返回的原始静态 URL 当作用户可访问的预览地址。

`init` 会执行数据库迁移、创建专用服务用户/DRF token、导入初始编码配置，但不输出凭证。重跑初始化不自动轮换 token、不重置已存在用户密码、不覆盖已调整的编码配置。前台服务设置私有素材、关闭注册；Bridge 使用同一个服务 token，并负责受保护的派生文件下载与转写操作。

### 已部署服务的一键日常启动

完成本页的首次镜像构建、私有 `.env`、MediaCMS 初始化和所需模型准备后，可以在项目根目录运行 `python start.py`。启动器读取既有 `backend/.env`：只有 `LIBRARY_PROCESSING_ENABLED=true` 且对应 `LIBRARY_WISE_URL/TOKEN` 或 `LIBRARY_MEDIACMS_URL/TOKEN` 已配置时，才检查新服务。没有开启的服务保持原样。不要把真实 token 填入命令行或分享日志。

- 已启动的 WISE 通过带认证的 `/health` 核对桥接身份、固定上游版本声明和工作线程状态。冷启动后尚未执行任务时，`upstream_installation_verified=false` 是正常的未验证状态，不阻塞启动或触发重启；实际任务仍会检查固定源码安装，模型能力需要另行验收。启动器不会为健康检查导入模型。MediaCMS 通过带认证的桥接 `OPTIONS /api/inspiration/v1/media/` 与素材列表接口核对接线；不会创建测试素材。健康请求不跟随重定向，不把认证头发往另一个地址。
- 远程地址、HTTPS 或带路径前缀的自定义反向代理只检查接口，不启动本地替代服务。回环 HTTP 根地址也须与展开后 Compose 的本机发布端口和服务密钥一致；不一致时提示检查，不覆盖任意一份配置。
- 本地缺少私有 `deploy/media-services/.env`、镜像或成功的 MediaCMS `init` 容器记录时，会提示先按本页准备。一键启动不会生成、轮换本目录密钥，不会构建或拉取媒体服务镜像，不执行初始化迁移，也不运行模型下载脚本。
- 基础WISE和MediaCMS需要补启时，只选择缺失或已停止的服务，使用 `up -d --no-deps --no-build --pull never --no-recreate`。MediaCMS 先等待既有数据库和 Redis 健康，再补启 Web、worker、beat；`init` 不在日常启动列表中。已停止容器的环境或镜像名称与当前 Compose 不一致时拒绝自动恢复，要求人工核对；暂停、重启中的容器也不被强制处理。
- 本地 MediaCMS Web 已健康时，还会在能确认是本 Compose 的前提下补启缺失的后台服务；外部独立部署或无法访问 Docker 时只报告接口复用，不能把它当作 worker 已验证。原有运行中容器不会因本次启动而重新创建或重启。
- 本地 WISE 必须保持 HF/Transformers 离线模式。默认 `visual/audio` 沿用上述补启流程；配置含 `face/object` 时，日常启动仅能恢复既有、状态为 `exited` 且配置一致的容器：核对服务密钥、环境、GPU申请、数据卷和容器实际镜像ID与本地目标镜像ID一致，再对刚检查过的精确容器ID执行 `docker start`。容器缺失、仅创建未运行（`created`）、暂停/恢复中或任一身份漂移时拒绝，不会自动新建、替换或下载。即使容器在检查后被删除，也只会启动失败，不会创建替代服务。
- 恢复可选能力服务不会重新运行probe或加载模型来“验证”。许可、权重和当前实现的验收收据仍由桥接运行时逐请求闭锁；缺失/失效时可选能力继续不可用。首次启用face/object仍需独立部署与验收，不能把容器恢复或接口健康视为模型可用。本机已验收object开发配置因此可以在停机后恢复，人脸不会被启动器额外开启。
- 如果需要启动的 MediaCMS worker 开启了转写，启动器先核对已有 Whisper 缓存：使用现成镜像、已存在的只读缓存卷、禁网临时容器，读取已安装 Whisper 的模型清单并计算文件 SHA-256；不导入模型、不执行推理或下载。缓存缺失、摘要错误或无法确认时停止本次补启，先按本页准备模型。运行中的 worker 不会为此被中断。

Compose 展开配置和容器配置只在进程内读取，不写入 `.runtime/startup.log`；启动提示不包含 token 或上游响应正文。新服务未就绪会显示独立提示，主创作工作台仍可启动。**“接口已验证 / 容器已运行”不等于模型已准备、队列已经消费或素材处理通过**，真实任务状态和验收记录仍是判断依据。

如修改了容器环境、镜像或模型配置，应先确认没有在途任务，再按本页部署步骤主动应用；重复双击 `start.py` 不会替你重建正在运行的服务。不要用删除卷、重置密钥或重跑初始化来处理普通启动问题。

### 上传与转码的磁盘余量保护

`MEDIACMS_DISK_RESERVE_BYTES` 默认10GiB。桥接上传使用磁盘临时文件处理器，在写入每个最多1MiB的文件块之前检查累计字节上限以及临时盘、成品盘可用空间；超出文件上限返回413，余量不足或无法可靠读取容量返回507，关闭并删除本次正在写入的临时文件。全部上传完毕但尚未保存原片时，另在成品盘检查 `余量 + 原片大小`，为跨挂载复制留空间。此项不改变现有单文件100GiB服务上限，也不放大主系统10GiB入口限制。

初始化、编码和转写的Celery父包装在启动前检查临时盘和成品盘，随后以不超过1秒的等待间隔继续检查。余量不足时仅终止本次包装创建的独立进程组，并在确认没有其他活跃租约持有者后记录失败；恢复可用空间后可从主站重试，不自动循环重试占满磁盘。正常任务的上游FFmpeg/Whisper、租约与进程守卫保持原行为。

这些是软保护，不是Docker卷或文件系统硬配额。两个并发写入者、1秒采样间隔内的突发写入，以及Django把上传或成品从一个挂载复制到另一个挂载的过程仍有竞争窗口；异常中断的成品路径可能需要对账清理。它不承诺永远不出现ENOSPC，也不替代长片/批量容量评测。新增源代码与配置应在确认无在途任务后主动应用到Web/worker/beat，不能只改文件就宣称运行中进程已经采用。

初次初始化只启用 `h264-360`、`h264-720` 和 MP4 preview，保留原片。单 Celery worker 并发 1，内存上限 8 GiB、CPU 配额 4；Web 配额 2 GiB/2 CPU。配额是保护上限，不是能完成任意长视频的保证。临时文件、持久卷总量和上传配额需要 inspiration 的处理调度继续约束。

当前桥接对临时媒体按完整视频流式编码，不启用上游分片合并；不代表具备帧级/分片断点恢复。暂不导入的 HLS 和视频雪碧图处理在专用集成路径关闭，避免无消费方的重复解码、占用和删除竞争；poster、MP4 preview 与 360p/720p 派生文件仍保留。

数据库默认上限 2 GiB/2 CPU；Redis 上限 1 GiB/1 CPU，数据 maxmemory 256 MiB、`noeviction`，给持久化/重写缓冲留余量。容器日志使用 local driver，20 MiB × 3 轮转；各服务设置进程数和与内存相同的内存+swap 上限。2026-10-04 已在无在途任务时应用并实机核对 MediaCMS 全栈限制；WISE 的 12 GiB swap/内存、4 CPU、pids256 与日志轮转同样已核对生效。

这些资源上限不能替代磁盘硬配额；named volumes 默认没有容量上限。MediaCMS 当前取消等待已派发转码/转写终态，避免删掉 FFmpeg 仍在读取的文件，不是强制终止上游任务。持久化队列/目录也不保证早 ack 任务在强杀后自动恢复，租约和 stale 状态对账必须实际验收。详细缺口见 `docs/70-素材检索与处理集成计划.md` 的资源与恢复审计。

## WISE 独立检索服务

使用另一个隔离项目名 `inspiration-wise-services`，仅公开 `127.0.0.1:9671`。默认 CPU，单工作进程，12 GiB 内存配额和 100 GiB 工作区软上限；这不是磁盘硬配额。依照官方 WISE 环境建立固定源码的运行环境，再构建桥接镜像：

常驻查询默认缓存 1 个模型、2 个项目索引，FAISS 索引文件装载合计最多 512 MiB。可通过私有 `.env` 中 `WISE_QUERY_CACHE_MODELS`、`WISE_QUERY_CACHE_PROJECTS`、`WISE_QUERY_CACHE_INDEX_BYTES` 调整，并重建容器后生效；应先测内存，不能因为已有素材超过预算就无限调大。50小时约36万帧的单项目可能超过默认预算。最后小样本冷查询15.13秒/热查询0.152秒只证明缓存生效，不是大库性能承诺。

当前索引重建与查询使用同一重任务锁，重建期间搜索返回503忙碌状态，页面提示稍后重试。旧快照保留和原子发布用于避免半成品索引，尚未实现一边重建、一边查询旧索引的并行服务。

```powershell
docker build -f services/wise-bridge/Dockerfile.upstream -t inspiration-wise-upstream:fcfa443f-cpu services/wise-bridge
docker build -t inspiration-wise-bridge:fcfa443f-cpu services/wise-bridge
docker compose -f deploy/media-services/compose.wise.yml --env-file deploy/media-services/.env config --quiet
docker compose -f deploy/media-services/compose.wise.yml --env-file deploy/media-services/.env up -d --no-build
```

实际试构建发现上游宽泛的 `python>=3.10` 当前解出 Python 3.14，但其 `torch<2.9` 没有对应 wheel。`Dockerfile.upstream` 因此只在临时依赖文件里约束 Python 3.11，并采用官方 CPU 模式对应的 onnxruntime；`/opt/wise` 保持固定提交的干净 checkout，安装为 editable package，以便校验真实导入来源和提交。不要改装 PyPI 上同名但无关的 `wise` 包。模型缓存位于独立 `wise-cache` 卷，任务/输入副本/索引位于 `wise-data` 卷。

### 可选 NVIDIA GPU 运行环境

CPU 仍是默认。已准备支持 CUDA 的 Docker/驱动、镜像和所需模型后，在私有 `deploy/media-services/.env` 中明确设置 `WISE_RUNTIME=cuda`，日常 `python start.py` 才会叠加 `compose.wise.gpu.yml`。未设置、空值或 `cpu` 使用原 CPU Compose；只接受小写 `cpu` / `cuda` 字面值，支持 dotenv 的引号和行尾注释，不支持该开关引用其他变量。启动进程已有的 `WISE_RUNTIME` 优先于文件，包含显式空值。此开关不修改后端地址、密钥或数据库。

GPU overlay 使用 `inspiration-wise-bridge:fcfa443f-cu128`、构建参数 `WISE_BASE=inspiration-wise-upstream:fcfa443f-cu128`，申请 1 张 NVIDIA GPU。它沿用 `inspiration-wise-services` 项目、`wise` 服务、9671 回环端口以及原有 `wise-data` / `wise-cache` 卷；不会另开一份素材库。原有内存、CPU、离线模式和容量限制继续生效；GPU 显存没有因此获得隔离配额。

首次准备镜像需显式执行，启动器不会代为构建、拉取或下载模型：

```powershell
docker build -f services/wise-bridge/Dockerfile.cuda -t inspiration-wise-upstream:fcfa443f-cu128 services/wise-bridge
docker build --build-arg WISE_BASE=inspiration-wise-upstream:fcfa443f-cu128 -t inspiration-wise-bridge:fcfa443f-cu128 services/wise-bridge
docker compose -f deploy/media-services/compose.wise.yml -f deploy/media-services/compose.wise.gpu.yml --env-file deploy/media-services/.env config --quiet
```

首次部署或从 CPU 主动切换时，先核对没有在途任务，再使用同一组 Compose 文件主动执行 `up -d --no-build --pull never wise`；这是人工部署动作，会按需重建容器，但保留数据卷。**仅修改 `WISE_RUNTIME` 后重复启动不会切换运行中的服务。** 已健康的服务始终复用；停止中的容器必须与所选 Compose 的镜像、环境、数据卷和 GPU 申请全部匹配，才会用 `--no-recreate` 恢复。缺少 overlay、未准备镜像或容器仍使用另一运行环境时，启动器提示检查并保留现状，不退回另一种运行环境，也不重置卷。

镜像存在或 GPU 设备可见只代表部署条件，不能据此宣称长片速度、显存容量或检索质量通过；这些仍需在当前运行环境用真实任务分别验证。

默认 `WISE_OFFLINE=1`，不会通过 HuggingFace 自动取权重；健康接口可验证进程与安装，但不证明模型已就绪。实际推理前需显式准备模型并记录 revision/checksum，然后验证真实查询。部分第三方模型不使用 HuggingFace 缓存，因此离线变量不能替代完整网络策略；在权重未准备时不提交这些模型任务。人脸和物体能力默认不开启。

视觉模型准备脚本位于 `services/wise-bridge/scripts/prepare_visual_model.py`，不带参数只显示锁定文件清单，`--download` 才下载。锁定 `timm/ViT-B-16-SigLIP2-512` 的固定 revision，仅准备 safetensors 和必要 tokenizer/config 文件，约 1.54 GB；不重复下载 `.bin`。准备时仅该命令覆盖 `HF_HUB_OFFLINE=0`，处理/查询服务保持离线，并把模型 revision/checksum 记入 `upstream-lock.json`。同样，Whisper 可先在 `www-data` 用户下把官方 `base` 权重准备到 `mediacms-cache`，确认摘要后在私有 `.env` 设置 `MEDIACMS_ENABLE_TRANSCRIPTION=true` 并重新创建服务。不得把模型文件可下载当作识别质量通过。

音频使用 `services/wise-bridge/scripts/prepare_audio_model.py` 与 `audio-model.lock.json`，固定 CLAP 2023/GPT-2 共约1.24GB；下载后 `--verify-cache` 在离线服务环境按每文件SHA256再验证。已完成的8.2秒合成音轨检索不代表中文音效或音乐相似度质量。固定WISE源存在音频索引枚举键错误，`upstream_compat.py` 仅在源码提交和目标函数SHA256完全匹配时局部修正，版本清单已记录此运行时适配。

本机若 DockerHub token 请求超时，而 Windows 系统代理访问正常，可以仅给本次构建进程继承已有 `HTTP_PROXY`/`HTTPS_PROXY`，不更改全局网络；代理凭证不应写入 Dockerfile、镜像或日志。

## 持久数据与容量

| 卷 | 内容 |
| --- | --- |
| `mediacms-db` | MediaCMS 独立 PostgreSQL |
| `mediacms-redis` | 持久任务队列 |
| `mediacms-media` | 处理中的原片副本、转码文件、缩略图与字幕；回存后清理 |
| `mediacms-static` | `collectstatic` 产物 |
| `mediacms-cache` | 可选 Whisper 模型、beat 调度文件 |
| `mediacms-tmp` | 上传临时文件、转码中间文件；需要失败清理和容量监控 |

卷的实际名字都有 `inspiration-media-services_` 前缀；与既有 `inspiration_*` 卷不同。

**目前不是零拷贝接入。** MediaCMS 默认 `FileField` 和 FFmpeg 管线使用自己的本地 `media_files`。通过上传 API 接入会临时增加一份原片，转码/上传临时文件还会增加占用；inspiration 在派生文件回存 CAS、任务结束后清理临时媒体。失败或运行中任务仍可能占用副本，必须计入峰值预算。不能仅挂同一个对象桶就宣称共享原片；需要单独适配存储后端、导入路径、安全权限和删除生命周期。WISE 桥接优先接收较小的分析副本，仍会持久保存其输入和索引，也要计入容量。

例如 50 Mbps、50 小时素材的原片约 1.125 TB（十进制），单片一小时处理时再临时占约 22.5 GB 原片副本。只有全量预先复制且未清理时才会在单原片层面接近 2.25 TB，所以队列必须限制正在转送和处理的数量。未计衍生物时，原片总量本身已经超过本机当时剩余空间，不能直接把这一规模作为试跑默认输入。应先用短素材验收，再逐级扩容并控制剩余空间。

业务上传入口当前默认单文件 10 GiB，业务转送层 50 GiB；MediaCMS 100 GiB 和 WISE 分析输入 8 GiB 不是用户上传额度。示例中 22.5 GB 的小时原片会被业务入口拒绝，开放大文件必须先验磁盘预约、断点和恢复，不应只把某一个上限调大。

## 2026-10-04 环境核对

- Windows + Docker Desktop，CLI/Server `29.5.2`，Compose `v5.1.4`，Linux backend。
- 最初 Docker 未启动，`docker-desktop` WSL2 处于 Stopped；自动启动遭策略拒绝。用户随后手动启动，`docker info` 已成功。
- NVIDIA RTX 4060 Laptop，显存 8188 MiB，驱动 `596.36`；主机内存约 63.3 GiB。
- C 盘可用空间当时约 1034 GiB，不能容纳前述双份 TB 级样本。
- 全局 Python `3.13.12`；WISE 应运行于独立容器/环境，不往业务环境混装其模型依赖。
- Docker 构建/短片处理/模型推理/长视频压力测试的实际结果分别记录，未运行项保持未验证。

## 许可与模型

MediaCMS 上游代码为 AGPL-3.0，WISE 为 Apache-2.0。保留原项目 LICENSE/NOTICE，并记录桥接修改与部署源码；服务分离本身不构成忽略上游许可的理由。模型权重许可另行登记：InsightFace 官方说明其默认预训练模型限非商业研究用途，因此人脸模型默认不自动启用。不能把 WISE 的代码许可等同于所有模型权重的许可。

上游依据：[MediaCMS release](https://github.com/mediacms-io/mediacms/releases/tag/v9.1.0)、[官方 Dockerfile](https://github.com/mediacms-io/mediacms/blob/1ab99af4587bfc515582783c9ee19ae12e0fd065/Dockerfile)、[WISE Dockerfile](https://github.com/ox-vgg/wise/blob/fcfa443fbb46eb361bb19151339338616838a5b5/Dockerfile)、[InsightFace 模型说明](https://github.com/deepinsight/insightface#license)。

## 可选人脸与物体的预检边界

发行配置默认只启用 `visual,audio`；开发机已单独准备固定OWLv2权重、记录开发测试用途并通过真实GPU probe，明确启用object，人脸仍关闭。只读预检默认读取 `/data/optional-models/{face,object}.json`，`WISE_OPTIONAL_USE` 默认`unspecified`。文件SHA/依赖/用途完整但没有当前实现的有效probe收据时，仍为`validation_required`、`ready=false`。手填验收JSON不能启用模型；每次关键实现或模型变化必须重新验收，不能继承旧镜像结论。主站状态页、提交/查询与推理子进程均执行闭锁，不阻止纯删除已有索引。可选模型不可用仍可新增基础视觉/声音索引的保证，仅适用于当前快照不含历史可选特征的项目；已有object特征时，普通增量可能被严格预检拒绝，旧HEAD保持。实际运行版本、短样本范围和该未修复边界见[物体检索验收记录](../../docs/71-可选人脸与物体检索验收边界.md)。

清单格式、InsightFace非商业研究与商业单独授权边界、HF缓存路径、摘要缓存失效和仅子进程网络保护见[可选模型预检说明](../../services/wise-bridge/OPTIONAL_MODELS.md)。本次没有修改私有env、下载新权重或开启可选能力。
