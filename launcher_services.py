"""Windows service lifecycle. Never infer ownership from a port or process name alone."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import subprocess
import time
from pathlib import Path, PureWindowsPath

PORTS = (8000, 5173, 15173, 25173, 35173)


def powershell(script):
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError(
            "无法读取或停止服务。请使用原启动账号；权限不足时右键启动脚本，以管理员身份运行。"
        )
    return result.stdout.strip()


def snapshot():
    if os.name != "nt":
        raise RuntimeError(
            "服务管理入口目前用于 Windows；其他系统请通过原服务管理器停止进程。"
        )
    raw = powershell("""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$processes = @(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,@{n='Started';e={$_.CreationDate.ToUniversalTime().ToString('o')}})
$listeners = @(Get-NetTCPConnection -State Listen | Select-Object LocalPort,OwningProcess)
@{processes=$processes;listeners=$listeners} | ConvertTo-Json -Depth 4 -Compress
""")
    return json.loads(raw)


def owned_processes(root: Path, processes):
    root = str(root.resolve()).replace("/", "\\").lower()
    python = root + "\\backend\\.venv\\scripts\\python.exe"
    vite = root + "\\frontend\\node_modules\\vite\\bin\\vite.js"
    selected = {}
    for p in processes:
        command = (p.get("CommandLine") or "").replace("/", "\\").lower()
        exe = (p.get("ExecutablePath") or "").replace("/", "\\").lower()
        # Match an actual argument, not an arbitrary substring in another checkout.
        frontend = PureWindowsPath(exe).name == "node.exe" and re.search(
            r'(?:^|\s)"?' + re.escape(vite) + r'"?(?:\s|$)', command
        )
        backend = exe == python and re.search(
            r"\s-m\s+(?:uvicorn\s+app\.main:app|celery\s+-a\s+app\.tasks\.celery_app\.celery_app\s+worker)(?:\s|$)",
            command,
        )
        if frontend or backend:
            selected[p["ProcessId"]] = p
    # venv launchers can start a child using the system Python executable.
    changed = True
    while changed:
        changed = False
        for p in processes:
            parent = selected.get(p["ParentProcessId"])
            if (
                parent
                and p["ProcessId"] not in selected
                and p.get("CommandLine")
                and p.get("Started", "") >= parent.get("Started", "")
            ):
                selected[p["ProcessId"]] = p
                changed = True
    return selected


def check_termination_access(processes):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    for p in processes:
        handle = kernel.OpenProcess(1, False, p["ProcessId"])
        if not handle:
            raise RuntimeError(
                f"无法停止项目进程 PID {p['ProcessId']}（Windows 权限不足或进程已变化）。请用原启动账号或右键以管理员身份运行“重启项目.bat”。"
            )
        kernel.CloseHandle(handle)


def manage(root: Path, action: str, emit=print):
    data = snapshot()
    owned = owned_processes(root, data["processes"])
    listeners = [p for p in data["listeners"] if p["LocalPort"] in PORTS]
    for port in PORTS:
        rows = [p for p in listeners if p["LocalPort"] == port]
        if rows:
            ids = sorted({p["OwningProcess"] for p in rows})
            ours = all(pid in owned for pid in ids)
            emit(
                f"[状态] 端口 {port}：{'本项目服务' if ours else '占用者身份未确认（可能权限不足）'}，PID {','.join(map(str, ids))}"
            )
    for pid in owned:
        emit(f"[状态] 已确认本项目进程 PID {pid}")
    if action == "status":
        if not listeners and not owned:
            emit("[状态] 本项目服务未运行。")
        return
    # Preflight everything before stopping any service, so a permission problem
    # cannot leave half the workspace stopped.
    unknown = [p for p in listeners if p["OwningProcess"] not in owned]
    if unknown:
        raise RuntimeError(
            "存在无法确认归属的端口占用，未停止任何服务。请用原启动账号运行；若旧服务由管理员启动，请右键“重启项目.bat”以管理员身份运行。不会按端口强杀其他程序。"
        )
    check_termination_access(owned.values())
    # Reverse discovery order terminates descendants before their launchers.
    for p in reversed(list(owned.values())):
        token = base64.b64encode(json.dumps(p).encode()).decode("ascii")
        powershell(f"""
$ErrorActionPreference = 'Stop'
$expected = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{token}')) | ConvertFrom-Json
$current = Get-CimInstance Win32_Process -Filter "ProcessId=$($expected.ProcessId)"
if ($current) {{
  if ($current.CreationDate.ToUniversalTime().ToString('o') -ne $expected.Started -or $current.CommandLine -cne $expected.CommandLine) {{ throw 'Process identity changed' }}
  Stop-Process -Id $current.ProcessId -ErrorAction Stop
}}
""")
        emit(f"[停止] 项目进程 PID {p['ProcessId']}")
    deadline = time.monotonic() + 10
    while True:
        remaining = snapshot()
        if not owned_processes(root, remaining["processes"]) and not any(
            row["LocalPort"] in PORTS for row in remaining["listeners"]
        ):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "服务仍在运行或被外部守护程序重新启动，请先停止其守护程序，再重启项目。"
            )
        time.sleep(0.5)
    emit("[停止] 前后端及项目 Worker 已停止；数据库、Redis、存储和数据均保留。")
