@echo off
chcp 65001 >nul
call "%~dp0启动项目.bat" --action restart %*
