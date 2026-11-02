@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title Portable Proxy — Setup / Repair
cd /d "%~dp0"

echo ╔══════════════════════════════════════════════════════╗
echo ║       PORTABLE PROXY — ONE-TIME SETUP               ║
echo ╚══════════════════════════════════════════════════════╝
echo.

:: ─── Step 1: Download Python 3.11.9 embeddable ─────────────────────────
if exist "python\python.exe" (
    echo [OK] Python runtime already present in python\ — skipping download.
    goto :patch_pth
)

echo [1/6] Downloading Python 3.11.9 embeddable (amd64)...
powershell -NoProfile -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
    "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip' " ^
    "-OutFile 'python-embed.zip' -UseBasicParsing"

if not exist "python-embed.zip" (
    echo [ERROR] Failed to download Python. Check your internet connection.
    pause
    exit /b 1
)

:: ─── Step 2: Extract ────────────────────────────────────────────────────
echo [2/6] Extracting to python\ ...
if exist "python" rmdir /s /q "python"
powershell -NoProfile -Command ^
    "Expand-Archive -Path 'python-embed.zip' -DestinationPath 'python' -Force"

if not exist "python\python.exe" (
    echo [ERROR] Extraction failed.
    pause
    exit /b 1
)

:patch_pth
:: ─── Step 3: Patch python311._pth to enable site-packages & parent dir ──
echo [3/6] Configuring python\python311._pth ...
powershell -NoProfile -Command ^
    "$content = @('python311.zip', '.', '..', 'Lib', 'Lib\site-packages', 'import site') -join \"`r`n\"; " ^
    "Set-Content -Path 'python\python311._pth' -Value $content -Encoding ASCII"

:: ─── Step 4: Download get-pip.py ────────────────────────────────────────
if exist "python\Scripts\pip.exe" (
    echo [OK] pip already installed — skipping.
    goto :install_deps
)

echo [4/6] Downloading get-pip.py ...
powershell -NoProfile -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
    "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' " ^
    "-OutFile 'get-pip.py' -UseBasicParsing"

if not exist "get-pip.py" (
    echo [ERROR] Failed to download get-pip.py.
    pause
    exit /b 1
)

:: ─── Step 5: Install pip ────────────────────────────────────────────────
echo [5/6] Installing pip ...
"python\python.exe" get-pip.py --no-warn-script-location
if errorlevel 1 (
    echo [ERROR] pip installation failed.
    pause
    exit /b 1
)

:install_deps
:: ─── Step 6: Install dependencies ───────────────────────────────────────
echo [6/6] Installing dependencies (fastapi, uvicorn, httpx, customtkinter) ...
"python\python.exe" -m pip install --no-warn-script-location fastapi "uvicorn[standard]" httpx customtkinter pystray Pillow
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

:: ─── Cleanup ────────────────────────────────────────────────────────────
if exist "python-embed.zip" del /q "python-embed.zip"
if exist "get-pip.py"       del /q "get-pip.py"

echo.
echo ╔══════════════════════════════════════════════════════╗
echo ║  Setup complete!                                     ║
echo ║  Run proxy.bat to start the Portable Proxy Manager.  ║
echo ╚══════════════════════════════════════════════════════╝
echo.
pause
