# Inspiration frontend DAEMON (keeps Vite dev server alive; auto-restarts on exit)
# 独立守护:vite 退出就自动重启;创建 .daemon-stop 文件可优雅停止。日志写 frontend.log。
#requires -Version 5.1
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

$stop = Join-Path $PSScriptRoot ".daemon-stop"
$log = Join-Path $PSScriptRoot "frontend.log"
$vitelog = Join-Path $PSScriptRoot "vite.log"

# 解析 npm 绝对路径(计划任务环境 PATH 可能不含 nodejs)
$npm = "C:\nvm4w\nodejs\npm.cmd"
if (-not (Test-Path $npm)) {
    $c = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($c) { $npm = $c.Source }
}

function Log($msg) {
    try { Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg" -Encoding UTF8 } catch {}
}

# 启动时清掉残留 vite(防止旧守护子进程占 5173)
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -match 'vite' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }
Start-Sleep -Seconds 1

Remove-Item $stop -ErrorAction SilentlyContinue
Log "frontend daemon up; npm=$npm"

while (-not (Test-Path $stop)) {
    Log "starting vite"
    & $npm run dev -- --host 127.0.0.1 --port 5173 *>> $vitelog
    if (Test-Path $stop) { break }
    Log "vite exited (code $LASTEXITCODE); restart in 2s"
    Start-Sleep -Seconds 2
}
Remove-Item $stop -ErrorAction SilentlyContinue
Log "frontend daemon stopped by flag"
