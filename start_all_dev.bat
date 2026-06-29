@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PROJECT_ROOT=%CD%"
set "PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [INFO] Python virtual environment not found. Running setup_env.bat...
  call "%PROJECT_ROOT%\setup_env.bat"
  if errorlevel 1 goto :fail_setup
)

if not exist "%PY%" goto :fail_missing_python
if not exist "%PROJECT_ROOT%\backend\app\main.py" goto :fail_missing_files
if not exist "%PROJECT_ROOT%\frontend\static\index.html" goto :fail_missing_files

if exist "%PROJECT_ROOT%\.env" (
  echo [INFO] Agent local config file found: %PROJECT_ROOT%\.env
) else (
  echo [WARN] Agent local config file is missing: %PROJECT_ROOT%\.env
  echo [WARN] Copy .env.example to .env and set API_KEY / MIMO_API_KEY when needed.
)

echo [INFO] Cleaning stale backend processes on port 8000...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$listenPids = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); $uvicornPids = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn*backend.app.main:app*--port 8000*' } | Select-Object -ExpandProperty ProcessId); $allPids = @($listenPids + $uvicornPids | Where-Object { $_ } | Sort-Object -Unique); foreach ($procId in $allPids) { try { Stop-Process -Id $procId -Force -ErrorAction Stop } catch {} }"

echo [INFO] Cleaning stale frontend processes on port 5500...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$listenPids = @(Get-NetTCPConnection -LocalPort 5500 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); $httpPids = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*http.server 5500*frontend\static*' } | Select-Object -ExpandProperty ProcessId); $allPids = @($listenPids + $httpPids | Where-Object { $_ } | Sort-Object -Unique); foreach ($procId in $allPids) { try { Stop-Process -Id $procId -Force -ErrorAction Stop } catch {} }"
timeout /t 1 /nobreak >nul

echo [INFO] Starting backend: http://127.0.0.1:8000
start /B "" "%PY%" -m uvicorn backend.app.main:app --app-dir "%PROJECT_ROOT%" --host 0.0.0.0 --port 8000 > "%PROJECT_ROOT%\backend.log" 2>&1
timeout /t 4 /nobreak >nul

echo [INFO] Verifying backend health and .env loading...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; try { $h=(Invoke-WebRequest -Uri 'http://127.0.0.1:8000/runtime/status' -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json; if($h.vision_backend.available_backends.video_understanding.mimo_video_ready -eq $true){$ok=$true} } catch {}; if($ok){ Write-Host '[OK] Backend healthy, .env loaded, MIMO ready.' } else { Write-Host '[WARN] Backend started but .env may not be loaded. Restarting...'; $pids=@(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); foreach($p in $pids){try{Stop-Process -Id $p -Force}catch{}}; Start-Sleep -Seconds 2; Start-Process -FilePath '%PY%' -ArgumentList '-m','uvicorn','backend.app.main:app','--app-dir','%PROJECT_ROOT%','--host','0.0.0.0','--port','8000' -WorkingDirectory '%PROJECT_ROOT%'; Start-Sleep -Seconds 4; Write-Host '[INFO] Backend restarted with correct Python environment.' }"

echo [INFO] Starting frontend: http://127.0.0.1:5500/index.html
start /B "" "%PY%" -m http.server 5500 --directory frontend\static > "%PROJECT_ROOT%\frontend.log" 2>&1
timeout /t 2 /nobreak >nul

echo [INFO] Recognition workers are no longer auto-started.
echo [INFO] Upload a video from the web UI, then open the matching monitor page to run recognition on that video.
echo [INFO] For a manual one-off worker, run start_webcam_demo.bat --camera-id cam_fence --source path\to\video.mp4
echo [INFO] The frontend will open immediately. If the backend is still warming up, the page will connect automatically.

start "" "http://127.0.0.1:5500/index.html"

echo [INFO] All dev services started successfully.
exit /b 0

:fail_setup
echo.
echo [ERROR] setup_env.bat failed.
echo.
pause
exit /b 1

:fail_missing_python
echo.
echo [ERROR] Virtual environment python is still missing: %PY%
echo.
pause
exit /b 1

:fail_missing_files
echo.
echo [ERROR] Project files are incomplete.
echo.
pause
exit /b 1
