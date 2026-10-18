# 4 核 / 4GB 单机部署

独立生产栈 `inspiration-production`，不读取本机开发 `.env`，不启动 MediaCMS、WISE 或旧推理服务。API 和单并发 Celery worker 共用持久工作目录，使用项目自带的 fs 存储；PostgreSQL、Redis AOF 分别使用独立卷。仅发布 HTTP 80 端口，不影响已有 1Panel。该单机方案不依赖 MinIO 的镜像供应，也减少内存占用。

## 首次准备

1. 服务器安装 Docker Compose、Python 3、flock，保留至少 3 GiB 空闲空间。
2. 将 `runtime.env.example` 按实际情况生成到 `/opt/inspiration/runtime.env`，权限 `600`。生产密钥必须随机生成并单独备份，密码使用十六进制避免数据库 URL 编码问题。
3. 将 `receive-release.sh` 安装为 `/opt/inspiration/receive-release.sh`。为 GitHub 单独生成 SSH 密钥；服务器 `authorized_keys` 中使用 `restrict,command="/opt/inspiration/receive-release.sh"` 限制此公钥，不能复用个人登录私钥。
4. 配置 GitHub `production` 环境的 Secrets：`DEPLOY_HOST`、`DEPLOY_PORT`、`DEPLOY_SSH_KEY`、`DEPLOY_KNOWN_HOSTS`。known_hosts 应来自已核对的服务器主机密钥，不能在流水线中盲目信任新密钥。
5. 手动运行 `Deploy production` 首次部署，或推送到 main 等待 `Regression checks` 全部通过后自动部署。手动运行用于首次部署与排错，不会等待另一条测试流水线。

GitHub 构建两个镜像，将镜像与生产 Compose、Nginx 配置一并经 SSH 流传到服务器。无需服务器访问私有镜像仓库或在 4GB 内存中编译项目。只有 main 的 push 测试成功才触发自动发布；PR 测试不会部署。工作流手动运行应选择 main。

## 运行与更新

入口为 `http://服务器IP`。首次安装不创建 demo，也不自动创建正式用户；通过服务器中的应用代码创建个人账号后，可执行 `python -m app.modules.website.admin grant 用户名` 授予平台管理权限。公网注册接口在生产 Nginx 中暂时关闭。

每次升级先加载新镜像，等待基础服务健康，再停止应用服务并创建数据库备份，然后运行数据库迁移、启动新应用并等待就绪。升级期间有短暂停机，正在运行的任务应提前结束；120 秒优雅退出不能保证任意长任务完成。迁移或健康检查失败会标记 GitHub 部署失败，不自动恢复数据库，也不宣称已经回滚。检查 `/opt/inspiration/backups`、容器日志和数据库版本后再决定恢复方式。

更新不会删除数据库、Redis、工作目录数据卷，也不会重新初始化密钥。仅清理较旧的应用镜像以及超过七天的升级前数据库备份。数据库备份不包含素材；素材与环境密钥需另做异地备份。不要执行 `down -v` 或 `docker system prune --volumes`。

生产配置默认单文件 512MiB、暂存总额 2GiB、预留 3GiB；这是业务软限制，不是磁盘硬配额。40GB 根盘仍需监控素材和卷占用。没有域名时仅有 HTTP，正式使用供应商密钥前应补域名和 HTTPS，或使用 SSH 隧道访问。

配置改动随下一次发布生效；运行密钥只在服务器配置，绝不提交仓库。域名/HTTPS 入口以及未来新增服务需要相应基础设施配置，不会仅凭业务代码推送自动出现。
