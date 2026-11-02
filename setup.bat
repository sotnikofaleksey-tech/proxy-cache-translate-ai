@echo off
setlocal
cd /d "%~dp0"
title Proxy Station - Setup / Repair

echo ============================================================
echo   PROXY STATION - ONE-TIME SETUP (needs internet, ~60 MB)
echo ============================================================
echo.
echo This installs a private copy of Python 3.11 INTO THIS FOLDER (python\).
echo Nothing is installed system-wide and no admin rights are needed.
echo.

rem --- 1. Python 3.11.9 with tkinter (extracted from the official MSI packages) ---
rem     (the "embeddable" zip has no tkinter, so the GUI could never start with it)
if exist "python\python.exe" (
    "python\python.exe" -c "import tkinter, ssl, sqlite3" >nul 2>&1
    if not errorlevel 1 (
        echo [OK] Python runtime already present - skipping download.
        goto install_pip
    )
    echo [INFO] Existing python\ is incomplete - rebuilding it.
    rmdir /s /q "python"
)

if exist "_setup" rmdir /s /q "_setup"
mkdir "_setup"

echo [1/4] Downloading Python 3.11.9 packages from python.org ...
for %%M in (core exe lib tcltk) do (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/amd64/%%M.msi' -OutFile '_setup\%%M.msi' -UseBasicParsing"
    if not exist "_setup\%%M.msi" (
        echo [ERROR] Download of %%M.msi failed. Check your internet connection and run setup.bat again.
        rmdir /s /q "_setup"
        pause
        exit /b 1
    )
)

echo [2/4] Unpacking into python\ ...
for %%M in (core exe lib tcltk) do (
    start /wait "" msiexec /a "%~dp0_setup\%%M.msi" /qn TARGETDIR="%~dp0python"
)
if exist "python\*.msi" del /q "python\*.msi"
rmdir /s /q "_setup"

"python\python.exe" -c "import tkinter, ssl, sqlite3" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was unpacked but is not working. Delete the python\ folder and run setup.bat again.
    pause
    exit /b 1
)

:install_pip
echo [3/4] Installing pip ...
"python\python.exe" -m ensurepip --upgrade --default-pip >nul 2>&1
"python\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip installation failed.
    pause
    exit /b 1
)

echo [4/4] Installing dependencies (fastapi, uvicorn, httpx, customtkinter, pystray, Pillow) ...
"python\python.exe" -m pip install --no-warn-script-location --disable-pip-version-check fastapi "uvicorn[standard]" httpx customtkinter pystray Pillow
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Setup complete. Run proxy.bat to start Proxy Station.
echo ============================================================
echo.
pause
