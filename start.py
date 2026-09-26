"""Inspiration 一键启动。双击运行，或执行 python start.py。

只使用 Python 标准库；沿用 backend/.env，不关闭已运行的服务、不清理数据卷。
前后端在后台运行，关闭本窗口不会停止服务。详细日志在 .runtime/ 中。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
API_URL = "http://127.0.0.1:8000"
WEB_URL = "http://127.0.0.1:5173"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# 本地健康检查不经过系统代理。
HTTP = build_opener(ProxyHandler({}))
CONFIG_QUERY = """
import json
from urllib.parse import urlsplit
from app.core.config import settings as s
d, r = urlsplit(s.database_url), urlsplit(s.redis_url)
m = urlsplit(('https://' if s.minio_secure else 'http://') + s.minio_endpoint)
print(json.dumps({
    'app_name': s.app_name,
    'database': [d.hostname, d.port or 5432],
    'compose_database': d.username == 'inspiration' and d.password == 'inspiration'
                        and d.path == '/inspiration',
    'redis': [r.hostname, r.port or 6379],
    'eager': s.celery_eager or s.generation_executor == 'local',
    'storage': s.storage_backend,
    'transcriber_url': s.transcriber_url,
    'library_indexer_url': s.library_indexer_url,
    'transcriber_token': s.transcriber_token,
    'library_indexer_token': s.library_indexer_token,
    'minio': [m.hostname, m.port or (443 if s.minio_secure else 80)],
}))
"""


class StartupError(RuntimeError):
    pass


def say(message: str) -> None:
    print(message, flush=True)


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def http_text(url: str) -> str:
    with HTTP.open(Request(url), timeout=2) as response:
        return response.read(1024 * 1024).decode("utf-8", errors="replace")


def api_ready(app_name: str) -> bool:
    try:
        data = json.loads(http_text(API_URL + "/health"))
        return (
            isinstance(data, dict)
            and data.get("status") == "ok"
            and data.get("app") == app_name
        )
    except (OSError, URLError, ValueError):
        return False


def web_ready() -> bool:
    try:
        html = http_text(WEB_URL)
        return "Inspiration" in html and 'id="root"' in html
    except (OSError, URLError):
        return False


def wait_for(label, check, timeout=90, process=None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise StartupError(f"{label} 提前退出，请查看 .runtime 下对应日志。")
        if check():
            return
        time.sleep(1)
    raise StartupError(f"等待{label}超时，请查看 .runtime 下对应日志。")


@contextmanager
def startup_lock(path: Path):
    """防止连续双击同时启动两份；进程退出后系统会自动释放锁。"""
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise StartupError(
                "另一个启动窗口正在准备服务，请等待它完成，不必重复点击。"
            ) from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Launcher:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.backend = root / "backend"
        self.frontend = root / "frontend"
        self.runtime = root / ".runtime"
        self.python = (
            self.backend
            / ".venv"
            / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        self.env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        self.started: list[subprocess.Popen] = []
        self.docker: str | None = None

    def command(self, args, *, cwd=None, timeout=120, capture=False, check=True):
        options = {
            "cwd": cwd or self.backend,
            "env": self.env,
            "stdin": subprocess.DEVNULL,
            "timeout": timeout,
            "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        }
        try:
            if capture:
                result = subprocess.run(
                    args,
                    check=False,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    **options,
                )
            else:
                with (self.runtime / "startup.log").open("ab") as log:
                    result = subprocess.run(
                        args,
                        check=False,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        **options,
                    )
        except subprocess.TimeoutExpired as error:
            raise StartupError(
                "操作超时，请检查服务或网络，详情见 .runtime/startup.log。"
            ) from error
        if check and result.returncode:
            raise StartupError(
                "操作未成功，请查看 .runtime/startup.log；现有配置和数据未被清理。"
            )
        return result

    def prepare(self):
        self.node = shutil.which("node")
        if not self.node:
            raise StartupError(
                "未找到 Node.js。请先安装 Node.js 20+，然后重新双击启动。"
            )
        version = self.command([self.node, "--version"], capture=True).stdout.strip()
        if int(version.lstrip("v").split(".")[0]) < 20:
            raise StartupError(f"当前 Node.js 为 {version}，需要 20 或更高版本。")
        if not self.python.exists():
            say("[准备] 创建后端 Python 环境……")
            self.command([sys.executable, "-m", "venv", str(self.backend / ".venv")])
        installed = self.command(
            [
                str(self.python),
                "-c",
                "import fastapi, uvicorn, sqlalchemy, alembic, psycopg, pydantic_settings, celery, redis, minio",
            ],
            check=False,
        )
        if installed.returncode:
            say("[准备] 首次安装后端依赖，可能需要几分钟……")
            self.command(
                [str(self.python), "-m", "pip", "install", "-e", ".[dev]"], timeout=900
            )
        env_file = self.backend / ".env"
        if not env_file.exists():
            content = (self.backend / ".env.example").read_text(encoding="utf-8")
            content = content.replace("@postgres:5432", "@127.0.0.1:5432")
            content = content.replace("redis://redis:", "redis://127.0.0.1:")
            content = content.replace(
                "MINIO_ENDPOINT=minio:", "MINIO_ENDPOINT=127.0.0.1:"
            )
            with env_file.open("x", encoding="utf-8") as handle:
                handle.write(content)
            say("[准备] 已从示例创建本地 .env；已有 .env 始终保留。")
        self.prepare_frontend()
        result = self.command(
            [str(self.python), "-c", CONFIG_QUERY], capture=True, check=False
        )
        if result.returncode:
            raise StartupError(
                "无法读取 backend/.env，请检查配置格式；不会覆盖您的配置。"
            )
        return json.loads(result.stdout)

    def prepare_frontend(self):
        """代码更新后同步依赖；只存在 Vite 不代表所有懒加载页面都可用。"""
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not npm:
            raise StartupError("未找到 npm，请修复 Node.js 安装后再启动。")
        manifest = self.frontend / "package.json"
        lock = self.frontend / "package-lock.json"
        fingerprint = hashlib.sha256(
            manifest.read_bytes() + b"\0" + (lock.read_bytes() if lock.exists() else b"")
        ).hexdigest()
        stamp = self.frontend / "node_modules/.inspiration-dependencies"
        if stamp.exists() and stamp.read_text(encoding="utf-8") == fingerprint:
            result = self.command(
                [npm, "ls", "--depth=0", "--include=dev"],
                cwd=self.frontend, capture=True, check=False,
            )
            if result.returncode == 0:
                return
        # 不在运行中的 Vite 下替换依赖，也不关闭可能属于其他项目的服务。
        if port_open("127.0.0.1", 5173):
            raise StartupError(
                "前端依赖需要同步。请先关闭端口 5173 的前端服务，再重新启动；后端和数据无需关闭。"
            )
        say("[准备] 正在同步前端依赖，可能需要几分钟……")
        self.command(
            [npm, "ci" if lock.exists() else "install", "--include=dev"],
            cwd=self.frontend, timeout=900,
        )
        self.command(
            [npm, "ls", "--depth=0", "--include=dev"],
            cwd=self.frontend, capture=True,
        )
        # npm install 可能首次生成锁文件，记录安装完成后的输入。
        fingerprint = hashlib.sha256(
            manifest.read_bytes() + b"\0" + (lock.read_bytes() if lock.exists() else b"")
        ).hexdigest()
        stamp.write_text(fingerprint, encoding="utf-8")

    def ensure_docker(self):
        if self.docker:
            return
        docker = shutil.which("docker")
        if not docker:
            raise StartupError(
                "缺少的基础服务需要 Docker，请安装并打开 Docker Desktop 后再启动。"
            )

        def ready():
            return (
                self.command(
                    [docker, "info", "--format", "{{.ServerVersion}}"],
                    timeout=15,
                    check=False,
                ).returncode
                == 0
            )

        if not ready():
            say("[服务] 正在启动 Docker Desktop……")
            result = self.command(
                [docker, "desktop", "start", "--detach"], timeout=30, check=False
            )
            if result.returncode:
                raise StartupError("无法自动启动 Docker Desktop，请手动打开它后重试。")
            wait_for("Docker Desktop", ready, timeout=120)
        self.docker = docker

    def ensure_service(self, name, endpoint, *, default_port, allowed=True):
        host, port = endpoint
        if not host:
            raise StartupError(f"{name} 地址不完整，请检查 backend/.env。")
        if port_open(host, port):
            say(f"[服务] {name} 已运行，复用现有服务。")
            return
        if host not in LOCAL_HOSTS or port != default_port or not allowed:
            raise StartupError(
                f"无法连接已配置的 {name} ({host}:{port})。请先启动该服务；不会改连另一份数据。"
            )
        self.ensure_docker()
        say(f"[服务] 正在启动 {name}（保留现有数据卷）……")
        self.command(
            [
                self.docker,
                "compose",
                "-f",
                str(self.root / "deploy/docker-compose.yml"),
                "up",
                "-d",
                "--no-deps",
                name,
            ],
            cwd=self.root,
            timeout=600,
        )
        wait_for(name, lambda: port_open(host, port))

    def dependencies(self, config):
        self.ensure_service(
            "postgres",
            config["database"],
            default_port=5432,
            allowed=config["compose_database"],
        )
        self.command(
            [
                str(self.python),
                "-c",
                (
                    "from app.core.database import engine; from sqlalchemy import text; "
                    "c = engine.connect(); c.execute(text('SELECT 1')); c.close()"
                ),
            ],
            timeout=30,
        )
        if config["storage"] == "minio":
            self.ensure_service("minio", config["minio"], default_port=9000)
            # 除了端口，还验证真实的存储凭据；不创建或清理 bucket。
            self.command(
                [
                    str(self.python),
                    "-c",
                    (
                        "from minio import Minio; from app.core.config import settings as s; "
                        "m = Minio(s.minio_endpoint, access_key=s.minio_access_key, "
                        "secret_key=s.minio_secret_key, secure=s.minio_secure); "
                        "m.bucket_exists(s.minio_bucket)"
                    ),
                ],
                timeout=30,
            )
        if not config["eager"]:
            self.ensure_service("redis", config["redis"], default_port=6379)
            self.command(
                [
                    str(self.python),
                    "-c",
                    (
                        "from redis import Redis; from app.core.config import settings; "
                        "Redis.from_url(settings.redis_url, socket_timeout=5).ping()"
                    ),
                ],
                timeout=15,
            )
        else:
            say("[服务] 当前为同步任务模式，无需额外启动 Redis / Worker。")

    def spawn(self, args, cwd, name):
        with (self.runtime / f"{name}.log").open("ab") as log:
            options = {
                "cwd": cwd,
                "env": self.env,
                "stdin": subprocess.DEVNULL,
                "stdout": log,
                "stderr": subprocess.STDOUT,
            }
            if os.name == "nt":
                options["creationflags"] = (
                    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                options["start_new_session"] = True
            process = subprocess.Popen(args, **options)
        self.started.append(process)
        return process

    def existing(self, port, label, ready):
        if not port_open("127.0.0.1", port):
            return False
        try:
            wait_for(label, ready, timeout=10)
        except StartupError as error:
            raise StartupError(
                f"端口 {port} 已被占用，但不是就绪的 Inspiration {label}。请检查占用，不会强行关闭其他程序。"
            ) from error
        say(f"[复用] {label}已经运行。")
        return True

    def worker(self):
        suffix = hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
        worker_name = f"inspiration-{suffix}@{socket.gethostname()}"

        def ready():
            result = self.command(
                [
                    str(self.python),
                    "-c",
                    (
                        "from app.tasks.celery_app import celery_app; "
                        f"result = celery_app.control.inspect(timeout=1, destination=[{worker_name!r}]).ping(); "
                        "raise SystemExit(0 if result else 1)"
                    ),
                ],
                timeout=10,
                check=False,
            )
            return result.returncode == 0

        if ready():
            say("[复用] 异步任务 Worker 已运行。")
            return
        say("[启动] 异步任务 Worker……")
        process = self.spawn(
            [
                str(self.python),
                "-m",
                "celery",
                "-A",
                "app.tasks.celery_app.celery_app",
                "worker",
                "--pool=solo",
                "-l",
                "info",
                "--hostname",
                worker_name,
                "--include",
                "app.tasks.generation_tasks",
            ],
            self.backend,
            "worker",
        )
        wait_for("Worker", ready, process=process)

    def optional_services(self, config):
        """Reuse preinstalled local inference environments; never download models at startup."""
        for name, key, port, model, env_key, token_key in [
            (
                "transcriber",
                "transcriber_url",
                8012,
                "faster-whisper-small",
                "ASR_MODEL",
                "transcriber_token",
            ),
            (
                "media-indexer",
                "library_indexer_url",
                8011,
                "qwen-embedding",
                "INDEX_MODEL",
                "library_indexer_token",
            ),
        ]:
            if config.get(key) not in (
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
            ):
                continue
            if port_open("127.0.0.1", port):
                continue
            python = (
                self.runtime
                / name
                / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )
            model_dir = self.runtime / "models" / model
            if not python.is_file() or not (model_dir / "config.json").is_file():
                continue
            service = self.root / "services" / name / "app.py"
            token_env = "ASR_TOKEN" if name == "transcriber" else "INDEX_TOKEN"
            # Use the backend's configured token without putting it in a command line or log.
            code = (
                "import os, sys, importlib.util; "
                f"os.environ[{env_key!r}] = {str(model_dir)!r}; "
                "os.environ['HF_HUB_OFFLINE'] = '1'; "
                f"sys.path.insert(0, {str(service.parent)!r}); "
                f"spec=importlib.util.spec_from_file_location('local_inference', {str(service)!r}); "
                "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
                f"import uvicorn; uvicorn.run(module.app, host='127.0.0.1', port={port})"
            )
            say(f"[启动] 本地 {name} 服务（使用已有模型）……")
            previous = self.env.get(token_env)
            self.env[token_env] = config.get(token_key) or ""
            try:
                self.spawn([str(python), "-c", code], self.backend, name)
            finally:
                if previous is None:
                    self.env.pop(token_env, None)
                else:
                    self.env[token_env] = previous

    def start(self):
        config = self.prepare()
        api_exists = self.existing(8000, "后端", lambda: api_ready(config["app_name"]))
        web_exists = self.existing(5173, "前端", web_ready)
        self.dependencies(config)
        self.optional_services(config)
        if api_exists:
            checked = self.command(
                [
                    str(self.python),
                    "-c",
                    "from app.core.health import database_ready; "
                    "import sys; sys.exit(0 if database_ready() else 1)",
                ],
                check=False,
                capture=True,
            )
            if checked.returncode:
                raise StartupError(
                    "数据库版本与代码不一致。请先停止旧后端，再重新启动以完成迁移；不会修改正在运行的数据库结构。"
                )
        if not api_exists:
            say("[准备] 检查数据库迁移和演示账号（不重置已有数据）……")
            self.command([str(self.python), "-m", "alembic", "upgrade", "head"])
            self.command([str(self.python), "-m", "app.seed"])
        if not config["eager"]:
            self.worker()
        if not api_exists:
            say("[启动] 后端……")
            process = self.spawn(
                [
                    str(self.python),
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                ],
                self.backend,
                "backend",
            )
            wait_for("后端", lambda: api_ready(config["app_name"]), process=process)
        if not web_exists:
            say("[启动] 前端……")
            process = self.spawn(
                [
                    self.node,
                    "--dns-result-order=ipv4first",
                    str(self.frontend / "node_modules/vite/bin/vite.js"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "5173",
                    "--strictPort",
                ],
                self.frontend,
                "frontend",
            )
            wait_for("前端", web_ready, process=process)

        # 通过前端实际使用的代理访问公开接口，确认前后端已经连通。
        def proxy_ready():
            try:
                data = json.loads(http_text(WEB_URL + "/api/v1/auth/login"))
                return data.get("detail") == "Method Not Allowed"
            except URLError as error:
                return getattr(error, "code", None) == 405
            except (OSError, ValueError, AttributeError):
                return False

        wait_for("前后端连接", proxy_ready, timeout=20)
        # Optional services may be absent without blocking the creative workspace.
        try:
            with HTTP.open(API_URL + "/health/ready", timeout=45) as response:
                diagnostic = json.load(response)
        except (OSError, ValueError) as exc:
            raise StartupError(
                "后端已启动，但依赖检查未通过，请查看 /health/ready 和运行日志。"
            ) from exc
        for name, status in diagnostic.get("optional", {}).items():
            if status != "ok":
                say(f"[提示] {name} 服务未就绪，相关自动处理暂不可用。")

    def cleanup_failed_start(self):
        # 只处理本次新建的直接子进程，绝不按进程名称或端口批量终止。
        for process in reversed(self.started):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="一键启动 Inspiration（已有服务会复用）"
    )
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--no-pause", action="store_true", help="完成后不等待按键，供终端/自动化使用"
    )
    args = parser.parse_args(argv)
    launcher = Launcher()
    code = 0
    try:
        say("Inspiration 一键启动\n")
        if sys.version_info < (3, 11):
            raise StartupError("需要 Python 3.11 或更高版本。")
        launcher.runtime.mkdir(exist_ok=True)
        with startup_lock(launcher.runtime / "start.lock"):
            launcher.start()
        say(f"\n启动成功！\n网页：{WEB_URL}\n接口文档：{API_URL}/docs")
        say(
            f"日志：{launcher.runtime}\n关闭此窗口不会停止服务，再次双击可复用已运行的服务。"
        )
        if not args.no_browser:
            try:
                webbrowser.open(WEB_URL)
            except webbrowser.Error:
                say("未能自动打开浏览器，请手动访问上方网页地址。")
    except (StartupError, OSError, ValueError, KeyboardInterrupt) as error:
        launcher.cleanup_failed_start()
        code = 1
        say(f"\n启动未完成：{error or '已取消启动'}\n日志目录：{launcher.runtime}")
        say("本次新启动的前后端已停止；原有服务及数据库、存储容器均保留。")
    if not args.no_pause and sys.stdin is not None and sys.stdin.isatty():
        try:
            input("\n按回车关闭此窗口……")
        except (EOFError, KeyboardInterrupt):
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
