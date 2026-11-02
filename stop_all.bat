@echo off
cd /d "%~dp0"
title Proxy Station - Stop

if not exist "python\python.exe" (
    echo Proxy Station is not installed here - nothing to stop.
    ping -n 4 127.0.0.1 >nul
    exit /b 0
)

echo Stopping Proxy Station (only processes started from this folder)...
"%~dp0python\python.exe" -m core.manager --stop-all
ping -n 3 127.0.0.1 >nul
exit /b 0
