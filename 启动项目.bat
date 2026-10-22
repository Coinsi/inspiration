@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "launch_action="
if "%~1"=="" set "launch_action=--action menu"
if exist "backend\.venv\Scripts\python.exe" (
    "backend\.venv\Scripts\python.exe" "start.py" %launch_action% %*
    exit /b
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "start.py" %launch_action% %*
    exit /b
)
where python >nul 2>nul
if not errorlevel 1 (
    python "start.py" %launch_action% %*
    exit /b
)
echo 未找到 Python。请安装 Python 3.11 或更高版本后重试。
pause
exit /b 1
