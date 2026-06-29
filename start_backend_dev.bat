@echo off
setlocal EnableExtensions
cd /d %~dp0

set "PROJECT_ROOT=%CD%"
set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [INFO] Python virtual environment not found. Running setup_env.bat...
  call "%PROJECT_ROOT%\setup_env.bat"
  if errorlevel 1 goto :fail_setup
)

if not exist "%PY%" (
  goto :fail_missing_python
)

if exist "%PROJECT_ROOT%\.env" (
  echo [INFO] Agent local config file found: %PROJECT_ROOT%\.env
) else (
  echo [WARN] Agent local config file is missing: %PROJECT_ROOT%\.env
  echo [WARN] Copy .env.example to .env and set API_KEY for cloud model access.
)

echo [INFO] Cleaning stale backend processes on port 8000...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$listenPids = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); $uvicornPids = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn*backend.app.main:app*--port 8000*' } | Select-Object -ExpandProperty ProcessId); $allPids = @($listenPids + $uvicornPids | Where-Object { $_ } | Sort-Object -Unique); foreach ($procId in $allPids) { try { Stop-Process -Id $procId -Force -ErrorAction Stop } catch {} }"
timeout /t 1 /nobreak >nul

echo [INFO] Starting backend in stable mode: http://127.0.0.1:8000
"%PY%" -m uvicorn backend.app.main:app --app-dir "%PROJECT_ROOT%" --host 0.0.0.0 --port 8000
exit /b 0

:fail_setup
echo.
echo [ERROR] setup_env.bat failed.
echo.
pause
exit /b 1

:fail_missing_python
echo.
echo [ERROR] Virtual environment python is still missing:
echo [ERROR] %PY%
echo.
pause
exit /b 1
