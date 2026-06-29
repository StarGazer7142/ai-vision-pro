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

echo [INFO] Starting frontend: http://127.0.0.1:5500/index.html
start "frontend-static" powershell -NoProfile -ExecutionPolicy Bypass -NoExit -Command "Set-Location -LiteralPath '%PROJECT_ROOT%'; & '%PY%' -m http.server 5500 --directory frontend\static"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5500/index.html"
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
