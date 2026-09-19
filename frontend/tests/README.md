# Frontend checks

`canvas-edge-create-live.mjs` requires `TEST_ISOLATED_PREVIEW=1`, `APP_URL`, `ARTIFACT_DIR`, `CANVAS_SOURCE_FIXTURE` (batch21 fixture) and `PLAYWRIGHT_MODULE`. It verifies the QA project name and creates/resets only the named drag-to-create QA canvas. Real pointer drags cover forward/reverse creation, port compatibility, cancellation, zoomed drop placement, keyboard choices, atomic undo/redo, persistence and existing connections. No model generation calls.

`canvas-grid-tools-live.mjs` requires `TEST_ISOLATED_PREVIEW=1`, `APP_URL`, `ARTIFACT_DIR`, `TEST_MEDIA_DIR`, `PLAYWRIGHT_MODULE`, and `CANVAS_SOURCE_FIXTURE` pointing to the batch21 canvas media fixture. It checks the exact QA project name and creates/resets only its dedicated named canvas. It writes real grid crops and uploads real image/MP4 versions, exercises a committed-but-lost split response and idempotent retry, undo/redo/reload, source preview, reference creation, invalid file rejection, upload failure recovery, drag/drop and actual video playback. No cloud model calls. Never use a user project as its source fixture.

`node tests/library-regression.mjs` intercepts every API request and tests library errors, full-prefix resume verification, retry after network failure and active-job deletion protection. It uses in-memory synthetic files and never uploads to a real backend.

`node tests/library-live.mjs` **writes real media and references**. Run only against an isolated preview with `TEST_ISOLATED_PREVIEW=1`, explicit `APP_URL`, `TEST_SOURCE`, and `ARTIFACT_DIR`. The source fixture must be a playable 3840px-wide video longer than three seconds; batch03 used a 30s, 386,833,369-byte generated H.264/AAC test pattern. The script creates a clearly named QA project, uploads, pauses/reloads/resumes, plays the preview, references a segment, checks deletion protection, releases the reference, trashes/restores the video and leaves an example reference. Optional `TEST_PROJECT_ID` resumes the playback/reference checks in that exact named QA project and skips creating/uploading; do not point it at user projects. No models are invoked. Use the demo account or `TEST_USERNAME`/`TEST_PASSWORD`; credentials never enter reports. A partial report is saved immediately with the new QA project id, allowing inspection after a failure.

Run `npm run build` for TypeScript and production bundling. Run `node --test tests/timeline-edit.test.mjs` for the existing editing checks.

For an interrupted QA upload, combine its `TEST_PROJECT_ID` with `TEST_RESUME_UPLOAD=1` to reselect `TEST_SOURCE`, verify the committed prefix, finish uploading and then run playback/reference checks. Only the initial pause step delays two chunk requests; subsequent upload traffic is not intercepted. Reports also track unexpected network failures and verify an original-file byte range against the local source.

The workspace browser checks run against the Vite dev server (default `http://127.0.0.1:5187`). They use an installed Playwright and Chrome. No package dependencies are installed by these scripts.

- `APP_URL`: development server URL.
- `ARTIFACT_DIR`: screenshots and JSON report directory; defaults to `test-results/workspace`.
- `PLAYWRIGHT_MODULE`: optional absolute path to an external `playwright/index.mjs`; otherwise resolves the installed `playwright` package.

`node tests/workspace-regression.mjs` intercepts all API traffic and tests loading, failures, scopes, navigation and persistence. Its HTML hook fixture is served by Vite, so React uses the same dependency instance as the imported hook. It is not included in the production build.

`node tests/workspace-live.mjs` checks a running backend using the existing documented demo account, or credentials in `TEST_USERNAME` and `TEST_PASSWORD`. Set `TEST_PROJECT_ID` to select an existing project. It allows login but blocks all other non-GET/HEAD API calls. It does not create test projects or run model requests. Reports never contain authentication tokens.

`node tests/media-browser-regression.mjs` uses intercepted API fixtures to verify asset sorting/selection/preview/retry, stale storyboard scopes, gallery/editor views, explicit prompt adoption and version comparison. Its generation panel fixture is not bundled for production.

`node tests/media-browser-live.mjs` requires `TEST_PROJECT_ID`. It reads existing assets and scenes, opens previews and compares existing image versions without selecting them. By default it never calls a model. Set `TEST_OPTIMIZER=1` only when a real model invocation is authorized: it sends one selected reference preview to the configured text model and adopts the returned text into a transient input. It allows no generation, version selection, deletion or entity edits. Optimization creates an audit entry and may incur provider cost. The report includes returned prompt text and reference identities, never credentials. Keep the dev source unchanged during a live run to avoid hot reload disrupting the browser request.

`library-search-regression.mjs` intercepts every API and checks search modes, coverage, pagination, errors and oversized image rejection. `library-search-live.mjs` uses the batch04 real-index fixture report and only its explicitly named isolated QA project; it runs real local semantic search and writes annotations, subtitles and one shot reference.

`timeline-tracks-live.mjs` requires `TEST_ISOLATED_PREVIEW=1`, `APP_URL`, `ARTIFACT_DIR`, `TEST_MEDIA_DIR` and `PLAYWRIGHT_MODULE`; it creates an isolated QA project, uses public image fixtures and a synthetic WAV/SRT, and verifies audio, subtitles, durable history and actual MP4 export. `timeline-transition-live.mjs` follows its report, checks the exact QA project name, and adds two real transitions and source previews. Its reset only clears transitions in that QA timeline. Both write screenshots and playable MP4 artifacts.


`transcription-live.mjs`: opt-in isolated project, actual local Chinese/English ASR, editable captions, reviewed publication and SRT. Requires Chinese/English fixture MP4s in ARTIFACT_DIR.

`library-materialize-live.mjs`: opt-in isolated project, VIDEO_FIXTURE, real image/video/audio extraction, original-source link, actual local ASR, caption-to-cut matching and rendered MP4. Does not call a cloud generation provider.

`workspace-polish-live.mjs`: read-only checks of named isolated QA projects. MATERIAL_REPORT points to a materialization report; covers catalog views/search/sorting, persisted groups, wide/mobile shot workspace and themes.

`script-draft-live.mjs`: creates a dedicated isolated QA project, checks reload recovery and conflict between browser draft and concurrent server edit. No model calls.

These scripts use TEST_ISOLATED_PREVIEW=1, APP_URL, ARTIFACT_DIR and PLAYWRIGHT_MODULE. Never aim their mutation scenarios at an existing user project.


- `library-picker-regression.mjs`：设置QA_PROJECT/QA_VERSION为已有完成转写的预览样本，先实读原片与字幕，再以受控51条目录验证分页、固定选择、迟到响应、失败重试和390px；不启动新的转写。
- `skill-draft-regression.mjs`：新建专用QA项目，验证本机草稿、技能切换取消、409并发保护、保存输入锁定、窄屏、非法目录拒绝和延迟读取/真实目录提交。
两者使用APP_URL、ARTIFACT_DIR、PLAYWRIGHT_MODULE和TEST_ISOLATED_PREVIEW=1；必须指向隔离预览。


- `library-reuse-live.mjs`：需要VIDEO_FIXTURE，创建两个隔离QA项目，实测原片复用/播放/引用、跨项目删除保护、回收恢复和移动布局；输出report.json供后续目录验收使用。
- `library-folders-live.mjs`：需要VIDEO_FIXTURE与REUSE_REPORT，创建QA项目，实测层级目录、指定目录上传与复用、批量移动且引用不变、目录编辑和非空删除保护。

- `studio-entry-live.mjs`：需要TEST_ISOLATED_PREVIEW=1、APP_URL、ARTIFACT_DIR、PLAYWRIGHT_MODULE、TEST_PROJECT_ID（已存在的隔离QA项目）。使用演示账号验证真实登录失败/成功、密码显示、项目搜索、首页与工作台想法传递、刷新与移动导航、明暗布局；不新建项目，不启动Agent或生成。仅登录请求修改会话。

- `generation-studio-regression.mjs`：需要APP_URL、ARTIFACT_DIR、PLAYWRIGHT_MODULE，拦截全部API；验证生成草稿刷新/账号/对象隔离、指令复用确认、图片参考参数、筛选、过期估算、提交锁定、失败重试和390px布局，不调用真实模型。
- `creative-workspace-live.mjs`：需要TEST_ISOLATED_PREVIEW=1、APP_URL、ARTIFACT_DIR、PLAYWRIGHT_MODULE；新建专用QA项目，使用真实离线Mock生成并钦定，验证镜头切换及延迟提交响应、画布分支复制的连线和目标、撤销/重做/保存恢复、分组与容量边界、明暗主题和窄屏。会保留QA项目与生成结果；不得指向主数据库。

- `shot-creation-flow-live.mjs`：需要同样的隔离预览变量与TEST_SOURCE（至少1.2秒的可解码MP4）。只新建专用QA项目，验证分镜直接创作、卡片引用及保存失败重试、真实原片上传/处理/播放/区间引用、同名对象搜索、提示词创建编辑与追加保护、草稿恢复及明暗390px。仅故障请求被拦截，不启动Agent、不调用云模型；会保留QA数据。后端须支持本地异步媒体处理。

- `canvas-media-workspace-live.mjs`：需要相同隔离预览变量和TEST_MEDIA_DIR（内含reference-a.jpg、reference-b.jpg、reference-video.mp4）。创建专用QA项目并实际上传媒体，验证节点播放/放大、固定版本、图片分支及端口连线、就地编辑、图片依赖类型保护、素材选择器、复制撤销和定点离线生成。ARTIFACT_DIR/fixture.json存在时仅复用并重置同名QA画布；不得用于用户项目。会保留QA作品，不调用云端模型。

- `canvas-creative-tools-live.mjs`：需要隔离预览变量、TEST_MEDIA_DIR、CANVAS_SOURCE_FIXTURE（批次21 fixture.json）；复用该命名QA项目，创建批次24专用画布。测试拖入/粘贴/响应丢失重试、智能引用、真实绘图裁切、同项目跨画布复制、素材库帧/视频提取、ZIP和只读浏览。保存产物于ARTIFACT_DIR。
- `canvas-tools-interactions-live.mjs`：使用上述ARTIFACT_DIR/fixture.json和已解压到offline的作品包，重置其专用“画布跨页粘贴验收”画布；实际拖动改接、对齐/吸附/撤销、离线Mock复用、只读草稿保留、明暗/窄屏、离线媒体播放。两套脚本有共享验收画布，不应并发运行。

- `project-covers-live.mjs`：需要 TEST_ISOLATED_PREVIEW=1、APP_URL、ARTIFACT_DIR、TEST_MEDIA_DIR 和 PLAYWRIGHT_MODULE；实际创建专用封面验收项目，验证自动编码、上传原图、响应丢失重试不重复、更换/移除/刷新、坏图修正、无封面创建、明暗及窄屏。不会修改已有项目。

- `project-trash-live.mjs`：需要TEST_ISOLATED_PREVIEW=1、APP_URL、ARTIFACT_DIR、PLAYWRIGHT_MODULE。只新建专用验收项目，检查取消删除、响应丢失后重试、刷新与创作选择隐藏、回收站和390px恢复、原地址重新访问；不删除已有项目。

- `director-workspace-live.mjs`：相同隔离预览变量，创建专用导演台项目，验证对象复制/快捷键保护、机位复制、取景小窗、常用视角/专注、构图线不进入PNG、取消渲染不上传、明暗与窄屏。`director-live.mjs`保留真实MP4、候选回填与草稿/历史回归，已适配参数页签。
- `timeline-workspace-live.mjs`：相同变量，读取ARTIFACT_DIR/report.json中`timeline-tracks-live.mjs`创建的专用QA项目并验证名称，只创建新验收时间线。测试新建/切换草稿保护、片段选择/拆分/复制/排序/撤销、时间线缩放、键盘页签、声音字幕跳转、保存失败重试和窄屏。顺序运行tracks、transition、workspace脚本，不与同项目的前两套回归并发。

- `director-motion.mjs`：Node 24直接读取TypeScript纯函数，验证旧插值、多机位精确采样/停留/匀速、总时长同比、独立编辑、插入边界与模板重置，不读写服务端。
- `director-keyframes-live.mjs`：需要TEST_ISOLATED_PREVIEW=1、APP_URL、ARTIFACT_DIR、PLAYWRIGHT_MODULE；新建独立QA项目，实际编辑4机位、播放暂停、PNG停留对比、分镜来源、MP4、撤销/草稿/历史恢复及明暗窄屏。生成multi-camera-motion.mp4，后续可按固定帧位置验证实际停留及移动，不调用云模型。

- `timeline-visuals-live.mjs`：相同隔离预览变量，TIMELINE_FIXTURE指向已完成的timeline-tracks-live报告；验证QA项目名称后只创建新的验收时间线，并向QA镜头上传测试视频版本。覆盖三条叠加轨的图片/视频、时长/裁切/透明度、布局时间预览、隐藏/撤销/重叠校验、保存刷新/历史、视频源越界及真实MP4，明暗和390px。不修改源验收时间线；输出visuals-report.json和visuals-film.mp4。

- `task-center-live.mjs`：需要TEST_ISOLATED_PREVIEW=1、APP_URL、ARTIFACT_DIR、TEST_MEDIA_DIR（beach.png与astronaut.png）和PLAYWRIGHT_MODULE；新建专用QA项目，实际上传27张图片，导出取消/重试/下载、离线Mock多结果、搜索分页/地址恢复、受控读取失败与迟到响应、明暗窄屏。后端必须异步执行（如预览进程CELERY_EAGER=false、GENERATION_EXECUTOR=local），不能使用同步eager模式验收取消。
- `task-center-boundaries-live.mjs`：读取上述ARTIFACT_DIR/report.json并验证QA项目名；还需要同目录task-music.mp3及2400×2400的size-limit.png。真实音频试听/下载、超尺寸图片处理失败/重试、另建空QA项目验证隔离及移动端视频播放。保留测试作品，不操作用户项目，不调用云模型。两套脚本顺序运行。

- `model-channels-live.mjs`：从仓库根目录运行，需要TEST_ISOLATED_PREVIEW=1、APP_URL、ARTIFACT_DIR、PLAYWRIGHT_MODULE和TEST_VIDEO（可解码MP4）。新建专用QA项目，启动临时本地HTTP上游，真实网页创建共享密钥渠道和四个模型，验证目录鉴权/空密钥保留、图片/视频模型及参数到达上游、视频回填播放、画布选择/保存、启停、并发冲突恢复、明暗窄屏。不调用付费云服务；结束时关闭临时上游，保留QA记录与产物。须对准已迁移0019的隔离预览。

- `writing-workspace-live.mjs`：需要TEST_ISOLATED_PREVIEW=1、APP_URL、ARTIFACT_DIR、PLAYWRIGHT_MODULE；新建专用项目和两部剧本，真实验证目录检索/排序、场景搜索/定位、专注切换保留正文和助手输入、保存刷新、明暗与390px、失败重试及键盘删除取消。仅目录503为受控故障，不调用云模型；配合script-draft-live.mjs回归草稿及并发保护。

- `story-workspace-live.mjs`：需要TEST_ISOLATED_PREVIEW=1、APP_URL、ARTIFACT_DIR、PLAYWRIGHT_MODULE；只新建专用项目。真实TXT导入、章节翻页/搜索/字号恢复，改编请求追加strategy=mock以验证真实离线生成及来源；设定CRUD、全文、筛选、转资产、小说回收恢复、手机目录/编辑、明暗和无横向溢出。仅保存/读取失败注入503，不调用付费云服务；输出report.json和截图。

- `timeline-interaction.mjs`：Node 24直接验证拖拽/裁切、重叠及长度边界、转场和音量包络、撤销，不访问服务。
- `timeline-playback-live.mjs`：需要隔离预览变量及TIMELINE_FIXTURE（timeline-visuals-live生成的visuals-report.json）。验证专用QA项目名称后只新建时间线，真实排序/裁切/跨轨/取消/撤销/保存、连续合成预览/音量/字幕/转场、暗亮手机与真实MP4导出，不改源验收时间线、不调用云模型。输出report.json、截图和drag-preview-film.mp4。

- `settings-workspace-live.mjs`：需要隔离预览变量，仓库根运行。新建QA项目，实测分区地址/刷新、封面上传、配额保存/失败重试/锁定、主题恢复、技能草稿跨入口和目录导入、明暗390px；只读界面以受控/me响应检查，不调用云模型。原skills-live与skill-draft-regression已适配独立/assets/import入口。
