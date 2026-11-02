@echo off
chcp 65001 >nul
title Proxy Station — Console Menu
cd /d "%~dp0"

"%~dp0python\python.exe" -m core.manager %*
pause
