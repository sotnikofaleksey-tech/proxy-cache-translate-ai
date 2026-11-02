@echo off
chcp 65001 >nul
title Proxy Station Launcher
cd /d "%~dp0"

if not exist "python\python.exe" (
    echo [INFO] Встроенный Python не найден. Выполняю первоначальную настройку setup.bat...
    call "%~dp0setup.bat"
)

:: Если передан аргумент --cli или cli, открываем консольный интерфейс
if /i "%~1"=="--cli" goto :cli_mode
if /i "%~1"=="cli"   goto :cli_mode

:: Запуск графического окна Proxy Station (pythonw — без фоновой консоли)
start "" "%~dp0python\pythonw.exe" -m core.gui
exit /b 0

:cli_mode
"%~dp0python\python.exe" -m core.manager %*
pause
