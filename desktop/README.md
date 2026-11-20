# Inspiration 桌面客户端

Windows / macOS 的联网桌面入口，直接加载 `https://coinisi.com/projects`。Electron 提供统一 Chromium 环境，现有网页负责业务界面；不打包 Python、数据库或另一份 React 前端。

## 与主分支并行开发

桌面代码在 `desktop/`，流水线在 `.github/workflows/desktop.yml`。Google 登录回跳另外使用网页入口和认证 API。

- 主分支的业务更新部署到云端后，客户端重新加载即可使用，不必重新打安装包。
- **业务分支中尚未部署的代码不会出现在客户端。** 客户端不是该 Git 提交的网页快照。
- 桌面窗口、Electron 运行时、安装器变化才需要发新版安装包。
- 客户端启动时检查正式 GitHub Release。发现适合当前系统和架构的新版，会提示下载，由用户自行安装。也可用“帮助 → 检查更新”，Windows 按 `Alt` 展开菜单。
- 窗口顶部的拖动区和原生窗口按钮随网站明暗主题调整，标题与菜单不再常驻窗口。macOS 菜单仍在系统菜单栏。
- 保留这个独立 worktree，原项目目录可以继续使用 `main`。后续按需把主分支合并到桌面分支；不要在原目录切换桌面分支来覆盖正在进行的工作。

## 本地运行与构建

需要 Node.js 24 和 npm。不需要开发者账号或付费签名服务。

```powershell
cd desktop
npm ci
npm test
npm run test:smoke
npm start
npm run dist:win
```

Windows 输出 `dist/Inspiration-<版本>-win-x64.exe`，安装时可选择目录。Mac 上分别运行：

```sh
npm run dist:mac -- --arm64
npm run dist:mac -- --x64
```

输出对应架构的 `.dmg`。建议使用对应架构的 Mac 构建、测试。

默认开发运行也连接生产网站。调试本地网站可设置 `INSPIRATION_DESKTOP_ORIGIN=http://127.0.0.1:5173`，然后运行 `npm start`。只允许 HTTPS 来源，开发模式额外允许 loopback HTTP；**安装包忽略该变量，固定连接生产网站**。

## 自动流水线

`Desktop packages` 使用三个 runner：Windows x64、macOS ARM64 和 macOS Intel。

1. 推送桌面分支中 `desktop/` 或桌面 workflow 的改动，会执行测试和打包；不会部署网站。
2. 完成后到 Actions 对应运行的 Artifacts 下载三个压缩包，解压得到安装包。测试产物保留 14 天。
3. 合并到默认分支之后，也可在 Actions 页面手动运行。`workflow_dispatch` 在 workflow 尚未进入默认分支时可能没有 UI 入口，分支 push 触发不受此限制。
4. 正式准备版本时更新 `desktop/package.json` 和 lockfile 的版本，再推送匹配的 `desktop-v0.1.0` 等标签。标签必须指向包含桌面代码和 workflow 的提交。
5. 标签构建成功后可以生成 **Release 草稿**，附三个安装包和 SHA256 校验值。也可以将验收过的分支构建产物原样上传到草稿。流水线保留已有 Release 的安装包，审核后再发布。

流水线使用 GitHub 自带 token，无须额外签名 Secrets。仓库或组织需要允许 Actions 运行以及发布 job 的 `contents: write`；私有仓库需要可用构建额度。

**本地修改不会自动出现在 GitHub，也不会自动启动流水线。** 需要提交并推送桌面分支。现有 `checks.yml` 仍可能随分支 push 运行；云端 `deploy.yml` 只接受 `main` 的部署条件。

## 登录、下载与边界

- 邮箱密码登录复用云端接口，客户端独立保存会话，不与系统浏览器共享登录状态。
- 从客户端点 **使用 Google 登录**，在系统浏览器授权后回到客户端，继续打开原来要访问的页面。浏览器若已登录 Google，可以选择已有账号。普通网页单独登录仍使用网页自己的会话。客户端支持取消和重试。
- 桌面 Google 登录需要部署带桌面登录入口的网页和认证 API。继续使用现有 `GOOGLE_REDIRECT_URI` 和 Web OAuth 配置，无需增加回调地址。云端功能还没上线时，客户端会结束等待，提示使用邮箱和密码或在浏览器使用网页版。
- 上传使用系统文件选择器，下载和 blob 导出使用保存对话框；不自动执行下载文件。
- 需联网，无离线编辑、后台本地渲染或本地数据库。
- 初次连接失败或页面进程退出显示恢复页，可重新连接；页面内 API 错误沿用网站现有处理。
- 仅云端同源页面留在应用中；外部链接经确认交给系统浏览器。相机、麦克风等系统权限本版不开放。
- 启用 context isolation、sandbox 和 web security，禁用渲染进程 Node 集成。授权桥仅接受客户端登录页主框架的开始、取消操作。浏览器回调使用临时 IPv4 回环端口，一次性授权码由发起请求的客户端持校验值在云端兑换。

## 未付费签名版本的安装说明

- Windows 安装包未签名，可能出现未知发布者或 SmartScreen 提示。受管理设备可能禁止安装。
- Mac 使用免费的 ad-hoc 签名，**不是 Apple Developer ID 签名，也没有公证**。首次打开可能需要按系统“隐私与安全性”的提示允许；受管理设备可能禁止。
- 不通过关闭系统整体保护来安装。仅在确认来源可信时允许单个应用。
- 这是直接分发测试包的方案，不等于应用商店上架。

## 验证

`npm test` 检查来源匹配、恶意 URL 与协议边界。`npm run test:smoke` 启动真实 Electron 和独立本地 fixture，验证沙箱、相对 API、登录存储、文件上传、blob 导出和断网恢复。先构建前端，再运行 `npm run test:google-auth` 验证分离浏览器会话中的授权回跳、桌面登录和取消重试。测试使用本地模拟接口，不使用真实账号。

这些测试不等于真实云端账号的端到端验收，也不替代两种 Mac 架构的实机安装。发布前还应使用自己的测试账号验证：登录与退出、素材上传、视频播放、画布编辑、导出、重启会话恢复，以及三种安装包在目标机器上的首次启动。

参考：[Electron 安全要求](https://www.electronjs.org/docs/latest/tutorial/security/)、[electron-builder](https://www.electron.build/)、[Apple 打开未认证应用](https://support.apple.com/en-au/102445)。
