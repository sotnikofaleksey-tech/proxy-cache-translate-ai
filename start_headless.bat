@echo off
chcp 65001 >nul
title Portable Proxy — Headless Server Mode
cd /d "%~dp0"

if not exist "python\python.exe" (
    echo [INFO] Portable Python runtime not found. Running setup.bat first...
    call "%~dp0setup.bat"
)

"python\python.exe" -m core.manager --headless
pause
