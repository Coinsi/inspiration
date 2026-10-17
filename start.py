"""Inspiration 一键启动。双击运行，或执行 python start.py。

只使用 Python 标准库；沿用 backend/.env，不关闭已运行的服务、不清理数据卷。
前后端在后台运行，关闭本窗口不会停止服务。详细日志在 .runtime/ 中。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
API_URL = "http://127.0.0.1:8000"
WEB_URL = "http://127.0.0.1:5173"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# 本地健康检查不经过系统代理。
HTTP = build_opener(ProxyHandler({}))


class NoRedirect(HTTPRedirectHandler):
    # Never forward private service credentials to a redirect destination.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def wise_runtime(private_env: Path, environ: dict) -> str:
    """Read only the literal runtime switch; never import private keys into os.environ."""
    value = environ.get("WISE_RUNTIME")
    if value is None:
        # Match complete assignments, including quoted multiline values of other
        # keys, so a line inside an unrelated secret cannot select a runtime.
        assignments = re.compile(
            r"^[ \t]*(?:export[ \t]+)?(?P<key>[A-Za-z_][A-Za-z_0-9]*)[ \t]*"
            r"=[ \t]*(?P<value>'(?:\\'|[^'])*'|\"(?:\\\"|[^\"])*\"|[^\r\n]*)"
            r"(?P<tail>[^\r\n]*)$",
            re.MULTILINE,
        )
        for match in assignments.finditer(private_env.read_text(encoding="utf-8-sig")):
            if match["key"] != "WISE_RUNTIME":
                continue
            value = match["value"].strip()
            if value.startswith(("'", '"')):
                if (
                    len(value) < 2
                    or value[-1] != value[0]
                    or (match["tail"].strip() and not match["tail"].lstrip().startswith("#"))
                ):
                    raise StartupError("WISE_RUNTIME 仅支持 cpu 或 cuda 的字面值。")
                value = value[1:-1]
            else:
                value = re.split(r"[ \t]+#", value, maxsplit=1)[0].strip()
                if value.startswith("#"):
                    value = ""
    value = "cpu" if value in {None, ""} else value
    if value not in {"cpu", "cuda"}:
        raise StartupError("WISE_RUNTIME 仅支持 cpu 或 cuda；未更改现有服务。")
    return value


PROCESSING_HTTP = build_opener(ProxyHandler({}), NoRedirect())
WISE_COMMIT = "fcfa443fbb46eb361bb19151339338616838a5b5"
PROCESSING_README = "deploy/media-services/README.md"
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
    'library_processing_enabled': s.library_processing_enabled,
    'library_wise_url': s.library_wise_url,
    'library_wise_token': s.library_wise_token,
    'library_mediacms_url': s.library_mediacms_url,
    'library_mediacms_token': s.library_mediacms_token,
    'minio': [m.hostname, m.port or (443 if s.minio_secure else 80)],
}))
"""
# Read only the installed Whisper manifest and existing cache. Do not import
# torch/whisper, instantiate a model, or invoke a downloader.
WHISPER_CACHE_CHECK = """
import ast, hashlib, importlib.util, pathlib, sys
source = pathlib.Path(importlib.util.find_spec('whisper').origin).read_text()
tree = ast.parse(source)
models = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == '_MODELS' for t in n.targets))
url = models[sys.argv[1]]
path = pathlib.Path('/cache/whisper') / url.rsplit('/', 1)[-1]
with path.open('rb') as f:
    actual = hashlib.file_digest(f, 'sha256').hexdigest()
raise SystemExit(0 if actual == url.split('/')[-2] else 1)
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
        return isinstance(data, dict) and data.get("status") == "ok" and data.get("app") == app_name
    except (OSError, URLError, ValueError):
        return False


def web_ready() -> bool:
    try:
        html = http_text(WEB_URL)
        return "Inspiration" in html and 'id="root"' in html
    except (OSError, URLError):
        return False


def processing_ready(name: str, url: str, token: str) -> bool:
    """Authenticated service identity checks, not model/queue readiness claims."""

    def read(path, method="GET"):
        request = Request(
            url.rstrip("/") + path,
            method=method,
            headers={"Authorization": ("Bearer " if name == "wise" else "Token ") + token},
        )
        with PROCESSING_HTTP.open(request, timeout=4) as response:
            return json.loads(response.read(1024 * 1024))

    try:
        if name == "wise":
            data = read("/health")
            return (
                isinstance(data, dict)
                and data.get("service") == "inspiration-wise-bridge"
                and data.get("upstream_commit") == WISE_COMMIT
                and data.get("worker_alive") is True
            )
        # This protected extension is specific to our MediaCMS integration.
        # OPTIONS checks permissions without uploading or creating any media.
        metadata = read("/api/inspiration/v1/media/", "OPTIONS")
        if not (
            isinstance(metadata, dict)
            and metadata.get("name") == "Upload"
            and "application/json" in metadata.get("renders", [])
            and "multipart/form-data" in metadata.get("parses", [])
        ):
            return False
        data = read("/api/v1/media")
        return (
            isinstance(data, dict)
            and isinstance(data.get("results"), list)
            and type(data.get("count")) is int
            and data["count"] >= 0
        )
    except (OSError, URLError, ValueError, TypeError):
        # Never log response bodies, request headers, or exception URLs.
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
            self.backend / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        self.env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        self.started: list[subprocess.Popen] = []
        self.docker: str | None = None
        self.service_results: dict[str, str] = {}
        self.reused_backend = False

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
            raise StartupError("操作未成功，请查看 .runtime/startup.log；现有配置和数据未被清理。")
        return result

    def prepare(self):
        self.node = shutil.which("node")
        if not self.node:
            raise StartupError("未找到 Node.js。请先安装 Node.js 20+，然后重新双击启动。")
        version = self.command([self.node, "--version"], capture=True).stdout.strip()
        if int(version.lstrip("v").split(".")[0]) < 20:
            raise StartupError(f"当前 Node.js 为 {version}，需要 20 或更高版本。")
        if not self.python.exists():
            say("[准备] 创建后端 Python 环境……")
            self.command([sys.executable, "-m", "venv", str(self.backend / ".venv")])
        self.prepare_backend()
        env_file = self.backend / ".env"
        if not env_file.exists():
            content = (self.backend / ".env.example").read_text(encoding="utf-8")
            content = content.replace("@postgres:5432", "@127.0.0.1:5432")
            content = content.replace("redis://redis:", "redis://127.0.0.1:")
            content = content.replace("MINIO_ENDPOINT=minio:", "MINIO_ENDPOINT=127.0.0.1:")
            with env_file.open("x", encoding="utf-8") as handle:
                handle.write(content)
            say("[准备] 已从示例创建本地 .env；已有 .env 始终保留。")
        self.prepare_frontend()
        result = self.command([str(self.python), "-c", CONFIG_QUERY], capture=True, check=False)
        if result.returncode:
            raise StartupError("无法读取 backend/.env，请检查配置格式；不会覆盖您的配置。")
        return json.loads(result.stdout)

    def prepare_backend(self):
        """Check declared requirements, including extras, before trusting an existing venv."""
        manifest = self.backend / "pyproject.toml"
        fingerprint = hashlib.sha256(manifest.read_bytes()).hexdigest()
        stamp = self.python.parent.parent / ".inspiration-dependencies"
        # Evaluate the current manifest, not potentially stale editable-install metadata.
        probe = """
import sys, tomllib
from importlib.metadata import version, PackageNotFoundError
from pip._vendor.packaging.requirements import Requirement
with open('pyproject.toml', 'rb') as stream:
    project = tomllib.load(stream)['project']
requirements = project['dependencies'] + project.get('optional-dependencies', {}).get('dev', [])
for raw in requirements:
    req = Requirement(raw)
    if req.marker and not req.marker.evaluate():
        continue
    try:
        installed = version(req.name)
    except PackageNotFoundError:
        sys.exit(1)
    if not req.specifier.contains(installed, prereleases=True):
        sys.exit(1)
import fastapi, uvicorn, sqlalchemy, alembic, psycopg, pydantic_settings
import celery, redis, minio, PIL, yaml, imageio_ffmpeg, httpx, pgvector
import jwt, bcrypt, multipart, email_validator, app
"""
        def valid():
            checked = self.command([str(self.python), "-c", probe], check=False, capture=True)
            if checked.returncode:
                return False
            return self.command(
                [str(self.python), "-m", "pip", "check"], check=False, capture=True
            ).returncode == 0

        changed = stamp.exists() and stamp.read_text(encoding="utf-8").strip() != fingerprint
        if not changed and valid():
            stamp.write_text(fingerprint, encoding="utf-8")
            return
        if port_open("127.0.0.1", 8000):
            raise StartupError(
                "后端依赖需要同步，但 8000 端口仍在使用。请等当前任务完成后停止后端及其 worker，"
                "再重新启动；不会在运行中替换 Python 依赖。"
            )
        say("[准备] 同步后端依赖……")
        self.command([str(self.python), "-m", "pip", "install", "-e", ".[dev]"], timeout=900)
        if not valid():
            raise StartupError("后端依赖安装后检查未通过，请检查 Python 环境及依赖版本。")
        stamp.write_text(fingerprint, encoding="utf-8")

    def summary(self):
        labels = {
            "ok": "接口检查通过（不代表模型推理已验证）",
            "unavailable": "未就绪", "not_configured": "未配置", "disabled": "未启用",
        }
        degraded = any(value == "unavailable" for value in self.service_results.values())
        say("\n基础工作台已就绪；部分服务未就绪。" if degraded else "\n基础工作台已就绪。")
        for name, status in self.service_results.items():
            description = labels.get(status, status)
            if name in {"media_processing", "library", "transcription", "agent", "canvas"} and status == "ok":
                description = "任务线程存活（不代表每个任务已成功）"
            say(f"  {name}：{description}")
        if self.reused_backend:
            say("[需确认] 本次复用了旧后端，无法确认它已加载最新代码和 .env。"
                "如刚更新过代码或配置，请等任务完成后重启后端及 worker。")

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
                cwd=self.frontend,
                capture=True,
                check=False,
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
            cwd=self.frontend,
            timeout=900,
        )
        self.command(
            [npm, "ls", "--depth=0", "--include=dev"],
            cwd=self.frontend,
            capture=True,
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
            raise StartupError("缺少的基础服务需要 Docker，请安装并打开 Docker Desktop 后再启动。")

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
            result = self.command([docker, "desktop", "start", "--detach"], timeout=30, check=False)
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
                # DETACHED_PROCESS can let venv child interpreters allocate a console.
                # CREATE_NO_WINDOW keeps the service and inherited child console hidden.
                options["creationflags"] = (
                    subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
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
                f"端口 {port} 已被占用，但不是就绪的 Inspiration {label}。"
                "请检查占用，不会强行关闭其他程序。"
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
                        "result = celery_app.control.inspect(timeout=1, "
                        f"destination=[{worker_name!r}]).ping(); "
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
                self.runtime / name / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
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
                "spec=importlib.util.spec_from_file_location('local_inference', "
                f"{str(service)!r}); "
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

    def processing_services(self, config):
        """Optional isolated stacks; absence must not break the creative workspace."""
        if not config.get("library_processing_enabled"):
            self.service_results.update(wise="disabled", mediacms="disabled")
            return
        for name in ("wise", "mediacms"):
            url = config.get(f"library_{name}_url")
            if not url:
                self.service_results[name] = "not_configured"
                continue
            self.service_results[name] = "unavailable"
            token = config.get(f"library_{name}_token")
            try:
                if not token:
                    raise StartupError("缺少服务认证配置，请检查 backend/.env。")
                self.ensure_processing_service(name, url, token)
                self.service_results[name] = "ok"
            except StartupError as error:
                say(f"[提示] {name} 自动处理未就绪：{error} 参见 {PROCESSING_README}。")
            except (OSError, ValueError, TypeError, KeyError):
                say(f"[提示] {name} 启动检查失败，未重置配置；请按 {PROCESSING_README} 检查部署。")

    def ensure_processing_service(self, name, url, token):
        try:
            parsed = urlsplit(url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or "\\" in url
                or any(ord(c) < 32 for c in url + token)
            ):
                raise ValueError
        except ValueError:
            raise StartupError("服务地址或认证格式无效，请检查 backend/.env。") from None

        ready = processing_ready(name, url, token)
        managed = (
            parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1"}
            and parsed.path in {"", "/"}
        )
        if not managed:
            if not ready:
                raise StartupError("已配置地址未通过认证与接口身份检查；不会改连本地服务。")
            say(f"[复用] {name} 已配置服务接口就绪（模型与任务能力另行验证）。")
            return
        if ready and name == "wise":
            say("[复用] WISE 接口与工作进程就绪；上游安装由实际任务检查，模型能力另行验证。")
            return
        if not ready and port_open(parsed.hostname, port):
            # Includes bad credentials, a still-starting stack, and unrelated apps.
            # Never run compose against an occupied but unverified endpoint.
            raise StartupError("端口已有服务，但认证或身份检查未通过；未重启或替换该服务。")

        folder = self.root / "deploy/media-services"
        private_env = folder / ".env"
        overrides = []
        if (
            name == "wise"
            and private_env.is_file()
            and wise_runtime(private_env, self.env) == "cuda"
        ):
            gpu_override = folder / "compose.wise.gpu.yml"
            if not gpu_override.is_file():
                raise StartupError("缺少 compose.wise.gpu.yml，未启动或切换 WISE 运行环境。")
            overrides = ["-f", str(gpu_override)]
        if ready:
            # A healthy local endpoint may belong to a manually deployed stack.
            # Only inspect/recover peer containers when the managed stack exists.
            docker = self.docker or shutil.which("docker")
            if not private_env.is_file() or not docker:
                say("[复用] MediaCMS 接口就绪；未检查独立部署的后台 worker。")
                return
            result = self.command(
                [docker, "info", "--format", "{{.ServerVersion}}"],
                capture=True,
                check=False,
                timeout=15,
            )
            if result.returncode:
                say("[复用] MediaCMS 接口就绪；Docker 不可达，未检查后台 worker。")
                return
            self.docker = docker
        else:
            if not private_env.is_file():
                raise StartupError("缺少私有 deploy/media-services/.env，请先完成部署准备。")
            self.ensure_docker()

        filename = "compose.wise.yml" if name == "wise" else "compose.yml"
        compose = [
            self.docker,
            "compose",
            "-f",
            str(folder / filename),
            *overrides,
            "--env-file",
            str(private_env),
        ]
        # config contains secrets: capture in memory, never use the normal log path.
        result = self.command(
            [*compose, "config", "--format", "json"],
            capture=True,
            check=False,
        )
        if result.returncode:
            raise StartupError("私有 Compose 配置不完整或不可解析，请先按文档准备。")
        definition = json.loads(result.stdout)
        expected_project = (
            "inspiration-wise-services" if name == "wise" else "inspiration-media-services"
        )
        if definition.get("name") != expected_project:
            raise StartupError("Compose 项目名与本集成不符；未创建另一套服务。")
        services = definition["services"]
        endpoint = services["wise" if name == "wise" else "web"]
        if not any(
            str(p.get("published")) == str(port) and p.get("host_ip") == "127.0.0.1"
            for p in endpoint.get("ports", [])
        ):
            raise StartupError("服务地址端口与本地 Compose 不一致；不会改连另一份部署。")
        env = endpoint.get("environment", {})
        key = "WISE_BRIDGE_TOKEN" if name == "wise" else "MEDIACMS_SERVICE_TOKEN"
        if env.get(key) != token:
            raise StartupError("主后端与私有 Compose 的服务密钥不一致；不会覆盖密钥。")
        optional_wise = False
        if name == "wise":
            capabilities = set(str(env.get("WISE_BRIDGE_CAPABILITIES", "")).split(","))
            if (
                str(env.get("HF_HUB_OFFLINE")) != "1"
                or str(env.get("TRANSFORMERS_OFFLINE")) != "1"
                or not capabilities <= {"visual", "audio", "face", "object"}
            ):
                raise StartupError("一键启动仅接受离线且能力名称明确的 WISE 配置。")
            optional_wise = bool(capabilities & {"face", "object"})
            devices = (
                endpoint.get("deploy", {})
                .get("resources", {})
                .get("reservations", {})
                .get("devices", [])
            )
            expected_devices = (
                [{"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}] if overrides else []
            )
            if devices != expected_devices:
                raise StartupError("WISE 的 GPU 申请与运行环境选择不一致；未修改容器。")

        def containers():
            result = self.command(
                [*compose, "ps", "--all", "--format", "json"],
                capture=True,
                check=False,
            )
            if result.returncode:
                raise StartupError("无法核对已有 Compose 容器；未修改运行服务。")
            raw = result.stdout.strip()
            rows = (
                json.loads(raw)
                if raw.startswith("[")
                else [json.loads(line) for line in raw.splitlines()]
            )
            return {row["Service"]: row for row in rows}

        current = containers()
        if optional_wise and current.get("wise", {}).get("State") != "exited":
            raise StartupError(
                "可选模型仅能恢复配置一致的既有已停止容器；请先单独部署与验收，不会自动新建。"
            )
        if ready and current.get("web", {}).get("State") != "running":
            say("[复用] MediaCMS 接口就绪，未接管不属于本 Compose 的服务。")
            return
        if name == "mediacms":
            initialized = current.get("init", {})
            if initialized.get("State") != "exited" or initialized.get("ExitCode") != 0:
                raise StartupError(
                    "MediaCMS 尚无成功的初始化记录，请先按文档部署；不会在启动时执行迁移。"
                )
            groups = [("db", "redis"), ("web", "worker", "beat")]
        else:
            groups = [("wise",)]
        pending = [
            s for group in groups for s in group if current.get(s, {}).get("State") != "running"
        ]
        optional_container = None
        optional_image = None
        for service in pending:
            existing = current.get(service)
            if not existing:
                continue
            if existing.get("State") not in {"exited", "created"}:
                raise StartupError("容器正在恢复或已暂停，请检查现有状态；不会干预该容器。")
            # --no-recreate intentionally preserves stopped containers too. Do
            # not accidentally resume old tokens or download-enabled settings.
            inspected = self.command(
                [self.docker, "inspect", existing["ID"]],
                capture=True,
                check=False,
            )
            if inspected.returncode:
                raise StartupError("无法核对已停止容器的配置；未恢复该容器。")
            inspected_container = json.loads(inspected.stdout)[0]
            if optional_wise:
                optional_container = inspected_container.get("Id")
                optional_image = inspected_container.get("Image")
                if (
                    not isinstance(optional_container, str)
                    or re.fullmatch(r"[0-9a-f]{64}", optional_container) is None
                    or not isinstance(optional_image, str)
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", optional_image) is None
                ):
                    raise StartupError("无法核对可选模型容器及镜像的固定身份；未恢复服务。")
            actual = inspected_container["Config"]
            actual_env = dict(
                value.split("=", 1) for value in actual.get("Env", []) if "=" in value
            )
            if actual.get("Image") != services[service]["image"] or any(
                actual_env.get(key) != str(value)
                for key, value in services[service].get("environment", {}).items()
            ):
                raise StartupError(
                    "已停止容器与当前 Compose 配置不一致，请手动核对部署；不会自动重建。"
                )
            if name == "wise":
                requests = inspected_container.get("HostConfig", {}).get("DeviceRequests") or []
                expected_requests = (
                    [
                        {
                            "Driver": "nvidia",
                            "Count": 1,
                            "DeviceIDs": [],
                            "Capabilities": [["gpu"]],
                            "Options": {},
                        }
                    ]
                    if overrides
                    else []
                )
                normalized_requests = [
                    {
                        "Driver": request.get("Driver"),
                        "Count": request.get("Count"),
                        "DeviceIDs": request.get("DeviceIDs") or [],
                        "Capabilities": request.get("Capabilities") or [],
                        "Options": request.get("Options") or {},
                    }
                    for request in requests
                ]
                if normalized_requests != expected_requests:
                    raise StartupError(
                        "已停止容器的 GPU 申请与 Compose 不一致；不会自动切换或重建。"
                    )
            for mount in services[service].get("volumes", []):
                if mount.get("type") != "volume":
                    continue
                expected_volume = definition.get("volumes", {}).get(mount["source"], {}).get("name")
                if not expected_volume or not any(
                    item.get("Destination") == mount["target"]
                    and item.get("Name") == expected_volume
                    for item in inspected_container.get("Mounts", [])
                ):
                    raise StartupError(
                        "已停止容器的数据卷与 Compose 不一致；不会改连或替换数据卷。"
                    )
        for image in {services[s]["image"] for s in pending}:
            checked = self.command(
                [self.docker, "image", "inspect", image, "--format", "{{.Id}}"],
                capture=True,
                check=False,
            )
            if checked.returncode:
                raise StartupError("所需镜像尚未准备；请按文档构建或拉取，一键启动不会自动下载。")
            if optional_wise and checked.stdout.strip() != optional_image:
                raise StartupError(
                    "已停止容器与本地目标镜像的固定 ID 不一致；请核对部署，不会自动切换镜像。"
                )
        if name == "mediacms" and "worker" in pending:
            self.check_whisper_cache(definition, services["worker"])

        for group in groups:
            selected = [s for s in group if s in pending]
            if selected:
                say(f"[服务] 补启 {name}：{', '.join(selected)}（不重建、不拉取镜像）……")
                # A checked optional container must never become a new deployment
                # if it disappears after inspection. Exact-ID start can only resume
                # that container; compose up could create a missing replacement.
                arguments = (
                    [self.docker, "start", optional_container]
                    if optional_wise
                    else [
                        *compose,
                        "up",
                        "-d",
                        "--no-deps",
                        "--no-build",
                        "--pull",
                        "never",
                        "--no-recreate",
                        *selected,
                    ]
                )
                result = self.command(
                    arguments,
                    cwd=self.root,
                    timeout=120,
                    capture=True,
                    check=False,
                )
                if result.returncode:
                    raise StartupError(
                        "服务补启失败；不会创建替代容器，请按文档检查。"
                        if optional_wise
                        else "Compose 补启失败；已有容器与数据均保留，请按文档检查。"
                    )

            def group_running(group=group):
                rows = containers()
                if optional_wise:
                    row_id = str(rows.get("wise", {}).get("ID", ""))
                    if len(row_id) < 12 or not optional_container.startswith(row_id):
                        return False
                return all(
                    rows.get(s, {}).get("State") == "running"
                    and rows[s].get("Health", "") in {"", "healthy"}
                    for s in group
                )

            wait_for(f"{name} {'/'.join(group)}", group_running, timeout=60)
        wait_for(name, lambda: processing_ready(name, url, token), timeout=60)
        say(f"[服务] {name} 接口已验证；容器已运行，模型效果与队列处理仍以任务验收为准。")

    def check_whisper_cache(self, definition, worker):
        """Before resuming queued ASR, verify the existing model without network."""
        env = worker.get("environment", {})
        if str(env.get("USE_WHISPER_TRANSCRIBE", "false")).lower() != "true":
            return
        mount = next(
            (
                v
                for v in worker.get("volumes", [])
                if v.get("target") == "/home/mediacms.io/.cache" and v.get("type") == "volume"
            ),
            None,
        )
        if mount is None:
            raise StartupError("无法确定 Whisper 缓存卷；未启动可能下载模型的 worker。")
        volume = definition.get("volumes", {}).get(mount["source"], {}).get("name")
        if not volume:
            raise StartupError("Whisper 缓存卷配置不完整，请先准备模型。")
        checked = self.command(
            [self.docker, "volume", "inspect", volume, "--format", "{{.Name}}"],
            capture=True,
            check=False,
        )
        if checked.returncode:
            raise StartupError("Whisper 缓存卷不存在，请先按文档准备模型。")
        checked = self.command(
            [
                self.docker,
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "--read-only",
                "--memory",
                "256m",
                "--cpus",
                "1",
                "--mount",
                f"type=volume,source={volume},target=/cache,readonly",
                "--entrypoint",
                "python",
                worker["image"],
                "-c",
                WHISPER_CACHE_CHECK,
                str(env.get("WHISPER_MODEL", "base")),
            ],
            capture=True,
            check=False,
            timeout=90,
        )
        if checked.returncode:
            raise StartupError(
                "Whisper 缓存缺失或摘要不符，请先准备模型；未启动会自动下载的 worker。"
            )

    def start(self):
        config = self.prepare()
        api_exists = self.existing(8000, "后端", lambda: api_ready(config["app_name"]))
        self.reused_backend = api_exists
        web_exists = self.existing(5173, "前端", web_ready)
        self.dependencies(config)
        self.optional_services(config)
        self.processing_services(config)
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
        if diagnostic.get("status") != "ok":
            raise StartupError("后端依赖或后台任务检查未通过，请查看 /health/ready。")
        if "background_workers" not in diagnostic:
            self.service_results["后台任务健康检查（旧后端需重启）"] = "unavailable"
        for name, status in diagnostic.get("background_workers", {}).items():
            self.service_results[name] = status
        for name, status in diagnostic.get("optional", {}).items():
            self.service_results[name] = status
            if status == "unavailable":
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
    parser = argparse.ArgumentParser(description="一键启动 Inspiration（已有服务会复用）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--no-pause", action="store_true", help="完成后不等待按键，供终端/自动化使用"
    )
    args = parser.parse_args(argv)
    launcher = Launcher()
    code = 0
    try:
        say("Inspiration 一键启动\n")
        if sys.version_info < (3, 11):  # noqa: UP036 - launcher runs before the project venv
            raise StartupError("需要 Python 3.11 或更高版本。")
        launcher.runtime.mkdir(exist_ok=True)
        with startup_lock(launcher.runtime / "start.lock"):
            launcher.start()
        launcher.summary()
        say(f"网页：{WEB_URL}\n接口文档：{API_URL}/docs")
        say(f"日志：{launcher.runtime}\n关闭此窗口不会停止服务，再次双击可复用已运行的服务。")
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
