@echo off
cd /d "%~dp0"
title Proxy Station

if not exist "python\python.exe" (
    echo [INFO] Bundled Python not found - running first-time setup...
    call "%~dp0setup.bat"
    if not exist "python\python.exe" exit /b 1
)

if /i "%~1"=="--cli" goto cli_mode
if /i "%~1"=="cli" goto cli_mode

rem GUI: pythonw = no console window. Errors are written to %LOCALAPPDATA%\ProxyStation\logs
start "" "%~dp0python\pythonw.exe" -m core.gui
exit /b 0

:cli_mode
"%~dp0python\python.exe" -m core.manager
pause
