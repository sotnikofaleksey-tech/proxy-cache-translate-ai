@echo off
cd /d "%~dp0"
title Proxy Station - Headless server

if not exist "python\python.exe" (
    echo [INFO] Bundled Python not found - running first-time setup...
    call "%~dp0setup.bat"
    if not exist "python\python.exe" exit /b 1
)

"%~dp0python\python.exe" -m core.manager --headless
pause
