# 4 核 / 4GB 单机部署

独立生产栈 `inspiration-production`，不读取本机开发 `.env`，不启动 MediaCMS、WISE 或旧推理服务。原文件使用 MinIO 对象存储，API 和单并发 Celery worker 共用持久暂存/导出目录；PostgreSQL、Redis AOF、MinIO 各有独立卷。项目默认只发布 `127.0.0.1:18080`，公网 80/443 由 1Panel 的 OpenResty 管理；MinIO 的 9000/9001 仅监听宿主回环地址。

MinIO 由 GitHub 从官方源码固定提交 `7aac2a2c5b7c882e68c1ce017d8256be2feea27f` 构建，不拉取已失效的旧 MinIO 镜像。构建层通过 GitHub Actions 缓存，普通业务更新复用编译结果；源码和许可证来源记录在镜像标签及 `/usr/share/doc/minio/LICENSE`。MinIO 内存上限 512MiB，Go 软内存目标 384MiB；需按实际上传负载观察。

## 首次准备

1. 服务器安装 Docker Compose、Python 3、flock，保留至少 3 GiB 空闲空间。
2. 将 `runtime.env.example` 按实际情况生成到 `/opt/inspiration/runtime.env`，权限 `600`。生产密钥必须随机生成并单独备份，密码使用十六进制避免数据库 URL 编码问题。
3. 将 `receive-release.sh` 安装为 `/opt/inspiration/receive-release.sh`。为 GitHub 单独生成 SSH 密钥；服务器 `authorized_keys` 中使用 `restrict,command="/opt/inspiration/receive-release.sh"` 限制此公钥，不能复用个人登录私钥。
4. 配置 GitHub `production` 环境的 Secrets：`DEPLOY_HOST`、`DEPLOY_PORT`、`DEPLOY_SSH_KEY`、`DEPLOY_KNOWN_HOSTS`。known_hosts 应来自已核对的服务器主机密钥，不能在流水线中盲目信任新密钥。
5. 手动运行 `Deploy production` 首次部署，或推送到 main 等待 `Regression checks` 全部通过后自动部署。手动运行用于首次部署与排错，不会等待另一条测试流水线。

GitHub 构建主 API、前端和 MinIO 三个镜像，将镜像与生产 Compose、Nginx 配置一并经 SSH 流传到服务器。无需服务器访问私有镜像仓库或在 4GB 内存中编译项目。只有 main 的 push 测试成功才触发自动发布；PR 测试不会部署。工作流手动运行应选择 main。

## 运行与更新

先在 1Panel 安装 OpenResty 并创建反向代理网站：域名 `coinisi.com`、代理地址 `http://127.0.0.1:18080`。参考 `openresty-site.conf.example`，保留 1Panel 生成的证书、ACME 和日志配置，设置 260MiB 请求上限、300 秒代理超时、关闭请求/响应缓冲，并覆盖转发客户端 IP 和协议。项目内部 Nginx 只信任回环及 Docker 默认 172.16.0.0/12 的代理来源；使用自定义 Docker 子网时应相应调整。项目不负责重启或重写面板管理的 OpenResty 配置。

旧版本从公网 80 直出切换时，先确认 OpenResty 安装准备与回退路径，再设置私有 runtime.env 的 `WEB_BIND=127.0.0.1`、`WEB_PORT=18080` 并重建 web 服务释放 80。不要在 OpenResty 已占用 80 后把项目重新改回直出。后续 GitHub 发布会继续使用内部端口，不争用 OpenResty 的 80/443。

域名 A 记录指向服务器公网 IP；修复错误的 NS/解析区以及旧 AAAA 记录后再在面板申请 HTTPS 证书。上线前可以通过 IP 或本机 Host 覆盖验证代理，域名尚未正确解析时不能宣称 HTTPS 已完成。

首次安装不创建 demo，也不自动创建正式用户。公网账号密码注册已开放，注册接口配置请求体大小限制与访问限流，应用层另有限流；新注册用户为普通账户。可执行 `python -m app.modules.website.admin grant 用户名` 为指定账户授予平台管理权限。邮箱验证和邮件找回密码尚未接通。

每次升级先加载新镜像，等待基础服务健康，再停止应用服务并创建数据库备份，然后运行数据库迁移、启动新应用并等待就绪。升级期间有短暂停机，正在运行的任务应提前结束；120 秒优雅退出不能保证任意长任务完成。迁移或健康检查失败会标记 GitHub 部署失败，不自动恢复数据库，也不宣称已经回滚。检查 `/opt/inspiration/backups`、容器日志和数据库版本后再决定恢复方式。

更新不会删除数据库、Redis、MinIO、工作目录数据卷，也不会重新初始化密钥。仅清理较旧的应用镜像以及超过七天的升级前数据库备份。数据库备份不包含素材；对象存储与环境密钥需另做异地备份。不要执行 `down -v` 或 `docker system prune --volumes`。

从此前 fs 试用版切换时，先备份 `/opt/inspiration/runtime.env`，设置 `STORAGE_BACKEND=minio`、`MINIO_ENDPOINT=minio:9000`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_BUCKET=inspiration`、`MINIO_SECURE=false`，保留原加密密钥和 app-data 卷。发布脚本在停写、备份数据库、执行结构迁移之后运行 `python -m app.maintenance migrate-fs-to-minio`：先核验全部本地源文件，再上传并回读校验每份副本，全部成功才提交数据库地址，原本地文件保留。失败会停止发布，不删除源文件；已上传但未提交的孤立对象不自动删除。后续发布重复执行时仅迁移尚为本地地址的对象。

管理控制台可通过 SSH 转发 `127.0.0.1:9001` 访问，凭据位于服务器私有 runtime.env；不要把对象存储管理端口直接开放公网。业务下载仍经过主站鉴权。

生产配置默认单文件 512MiB、暂存总额 2GiB、预留 3GiB；这是业务软限制，不是磁盘硬配额。40GB 根盘仍需监控素材和卷占用。没有域名时仅有 HTTP，正式使用供应商密钥前应补域名和 HTTPS，或使用 SSH 隧道访问。

配置改动随下一次发布生效；运行密钥只在服务器配置，绝不提交仓库。域名/HTTPS 入口以及未来新增服务需要相应基础设施配置，不会仅凭业务代码推送自动出现。

## Google 注册与登录

Google 登录作为独立入口使用现有用户和权限体系。新增迁移包含短期 OAuth 事务表，发布时仍执行现有数据库迁移流程。`/auth/providers` 只在配置完整有效时公开 Google；未配置的安装继续使用账号密码。

在服务器的私有 `/opt/inspiration/runtime.env` 中设置以下五项，不把凭据加入 Git、前端环境变量或镜像构建参数：

```dotenv
GOOGLE_CLIENT_ID=your-web-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-private-client-secret
GOOGLE_REDIRECT_URI=https://coinisi.com/api/v1/auth/google/callback
GOOGLE_FRONTEND_URL=https://coinisi.com
GOOGLE_REGISTRATION_ENABLED=true
```

Google Auth Platform 的 Web 客户端中，JavaScript 来源填写 `https://coinisi.com`，重定向 URI 填写上述完整回调地址。只申请 `openid email profile`。生产使用同一站点承载前端和 `/api/v1`，所有 OAuth Cookie 均使用 HttpOnly、SameSite=Lax 和 Secure；回调后的登录令牌通过同源 POST 换取，不写入 URL。代理应保留 Origin，并覆盖可信转发头。不要缓存 `/api/v1/auth/` 或 `/auth/google/complete`。面板 OpenResty 的回调访问日志也应关闭或去除查询参数，避免记录短期授权码。

`GOOGLE_REGISTRATION_ENABLED=true` 允许已验证邮箱的 Google 用户首次登录时创建普通账户，不授予平台管理员或任何现有项目权限。设为 `false` 后已有绑定仍可登录，已登录账户仍可绑定。此开关独立于账号密码注册，不控制密码注册入口。已有邮箱不会自动合并：用户需先通过原登录方式进入账户安全设置，再主动绑定 Google。Google 创建的无密码账户使用 Google 登录，当前不提供设置本地密码或解绑入口。

正式公开使用前，发布可访问的隐私政策等必要页面并配置 Google 品牌信息，按控制台提示完成发布/验证。客户端 JSON 只提供凭据和允许的地址，不能证明这些控制台设置或真实用户授权已完成。配置后发布新版本，再用浏览器完整验证取消授权、首次注册、再次登录、原账号绑定以及退出。

若另外需要本机测试，应为开发配置独立客户端及相同来源的回调，例如前端 `http://localhost:5173` 和经 Vite 代理的 `http://localhost:5173/api/v1/auth/google/callback`；不能把正式回调与本地前端混用。开发配置不要修改正式服务器的值。
