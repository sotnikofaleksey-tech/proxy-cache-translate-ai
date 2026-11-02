@echo off
chcp 65001 >nul
title Stop All Configured Proxies
cd /d "%~dp0"

set "CFGFILE=%LOCALAPPDATA%\ProxyStation\config\proxies.json"
if not exist "%CFGFILE%" (
    echo [WARN] Config not found at %CFGFILE% — nothing to stop.
    timeout /t 3 >nul
    exit /b 0
)

echo Stopping all configured proxy ports...
powershell -NoProfile -Command ^
  "$cfg = Get-Content '%LOCALAPPDATA%\ProxyStation\config\proxies.json' -Raw | ConvertFrom-Json; foreach ($p in $cfg.proxies) { $port = $p.listen_port; $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue; if ($conns) { foreach ($pid_val in ($conns.OwningProcess | Select-Object -Unique)) { Write-Host ('[STOP] Port ' + $port + ' (' + $p.name + ') -> PID ' + $pid_val); taskkill /F /T /PID $pid_val 2>&1 | Out-Null } } else { Write-Host ('[OK] Port ' + $port + ' (' + $p.name + ') is already free.') } }"

timeout /t 2 >nul
