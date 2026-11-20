# 本地验证记录

日期：2026-10-23（Asia/Shanghai）。基础提交：`6f93785`。桌面分支：`codex/desktop-clients`。

## 已完成

- `npm test`：3 项通过，覆盖来源校验、仿冒域名、非 Web 协议和 Google 授权域名识别。
- `npm run test:smoke`：Windows 上启动真实 Electron，沙箱与 Node 隔离、同源 API、localStorage 刷新保留、文件上传、blob 下载内容、禁止 file 导航、断网恢复页及重新连接全部通过。fixture 不使用生产账号。
- 真实云端只读检查：独立临时用户目录打开 `https://coinisi.com/projects`，正常跳转登录页并显示邮箱密码输入框；检查截图通过，未登录、未创建数据。
- `npm run dist:win`：成功生成 `Inspiration-0.1.0-win-x64.exe`，112,114,650 字节；Authenticode 状态为 `NotSigned`。
- 安装包 SHA256：`A95A7CD3E74FFD91F65951B399491724F09B009F045FFDC04D5C2AE364B37B24`。
- workflow YAML 解析成功，包含三组构建目标和仅标签触发的 Release 草稿任务。
- `npm audit --omit=dev`：0 项。完整开发依赖审计仍有 8 项 moderate，来自打包工具的 `@electron/get → global-agent → roarr → sprintf-js` 链；这些依赖不打入应用。没有使用会降级打包工具的强制自动修复。

## Google 桌面登录验证（2026-11-21）

- 前端生产构建和 59 项前端测试通过。Windows x64 目录打包确认包含授权模块与 preload。
- Google 后端 75 项测试通过，覆盖现有网页登录与绑定、凭据过期、错误校验值、并发重复兑换，以及取消后旧回调不影响新尝试。
- `npm run test:google-auth` 六项通过。真实 Electron 登录页连接独立 Chrome 与实际回环端口，验证取消、重试、错误状态、会话隔离、返回原页面与刷新恢复。证据在 `desktop/test-results/google-auth-verified/report.json`。
- Google 与云端 API 使用测试数据，测试捕获系统浏览器打开请求后交给独立浏览器执行。正式 Google 账户、系统默认浏览器调起和 macOS 实机仍待验证。

## 尚未验证

- GitHub Actions 尚未运行：本地更改未提交、未推送。
- Mac ARM64 / Intel 安装包尚未在 macOS 上构建或实机安装。
- Windows 安装器已生成，未在用户系统执行安装。
- 未使用真实账号完成创作、生成、视频剪辑等业务端到端验收。
- 上述安装包和云端加载记录产生于 Google 桌面授权回跳实现前。验证回跳时应使用更新后的安装包和已经部署相应 API 的站点。离线编辑和安装包自动更新尚未实现。

截图位于本地忽略目录 `desktop/test-results/offline.png` 和 `desktop/test-results/cloud-login.png`；流水线会保存其 fixture 测试截图。
