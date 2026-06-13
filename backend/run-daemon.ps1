# Inspiration backend DAEMON (keeps backend alive; auto-restarts on exit)
# 独立守护:uvicorn 退出就自动重启;创建 .daemon-stop 文件可优雅停止。
# 不带 --reload(本机 reload 不可靠);改代码后 kill 掉 python uvicorn 进程,守护会用新代码自动拉起。
# 日志:守护事件 -> backend.log(追加写,不长期持锁);uvicorn 输出 -> uvicorn.log。
#requires -Version 5.1
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$stop = Join-Path $PSScriptRoot ".daemon-stop"
$log = Join-Path $PSScriptRoot "backend.log"
$uvlog = Join-Path $PSScriptRoot "uvicorn.log"

$bind = if ($args.Count -ge 1) { $args[0] } else { "127.0.0.1" }
$port = if ($args.Count -ge 2) { $args[1] } else { "8000" }

function Log($msg) {
    # 每行 open-write-close,绝不长期持有日志句柄(避免重启重叠时互锁)
    try { Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg" -Encoding UTF8 } catch {}
}

# 启动时先清掉任何残留的 uvicorn(防止旧守护的子进程占着 8000)
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'uvicorn app.main' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }
Start-Sleep -Seconds 1

Remove-Item $stop -ErrorAction SilentlyContinue
Log "daemon up; guarding ${bind}:${port}"

while (-not (Test-Path $stop)) {
    Log "starting uvicorn"
    # uvicorn 输出独立写 uvicorn.log(只在本次运行期间持锁,守护事件日志不受影响)
    & $python -m uvicorn app.main:app --host $bind --port $port *>> $uvlog
    if (Test-Path $stop) { break }
    Log "uvicorn exited (code $LASTEXITCODE); restart in 2s"
    Start-Sleep -Seconds 2
}
Remove-Item $stop -ErrorAction SilentlyContinue
Log "daemon stopped by flag"
