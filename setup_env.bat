@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d %~dp0

set "PROJECT_ROOT=%CD%"
set "REQUIREMENTS=%PROJECT_ROOT%\requirements.txt"
set "VENV_DIR=%PROJECT_ROOT%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "PY_CMD="
set "PY_LABEL="
set "PY_VERSION="

if not exist "%REQUIREMENTS%" (
  call :fail "requirements.txt not found." "Please run this script inside the project root."
)

call :detect_python
if errorlevel 1 exit /b 1

for /f %%v in ('!PY_CMD! -c "import sys; print(sys.version.split()[0])"') do set "PY_VERSION=%%v"
echo [INFO] Using Python !PY_VERSION! via !PY_LABEL!

if not exist "%VENV_PY%" (
  echo [INFO] Creating virtual environment...
  !PY_CMD! -m venv "%VENV_DIR%"
  if errorlevel 1 (
    call :fail "Failed to create .venv." "Please confirm Python includes the venv module."
  )
)

if not exist "%VENV_PY%" (
  call :fail "Virtual environment python not found." "%VENV_PY%"
)

echo [INFO] Bootstrapping pip...
"%VENV_PY%" -m ensurepip --upgrade >nul 2>nul
"%VENV_PY%" -m pip --version >nul 2>nul
if errorlevel 1 (
  call :fail "pip is unavailable in the virtual environment." "Please reinstall Python 3.10 or 3.11."
)

echo [INFO] Upgrading pip/setuptools/wheel...
"%VENV_PY%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
  call :fail "Failed to upgrade pip toolchain." "Please check internet access or Python installation."
)

if exist "%PROJECT_ROOT%\vendor\wheels\*.whl" (
  echo [INFO] Installing dependencies from local wheelhouse...
  "%VENV_PY%" -m pip install --no-index --find-links "%PROJECT_ROOT%\vendor\wheels" -r "%REQUIREMENTS%"
) else (
  echo [INFO] Installing dependencies from internet...
  "%VENV_PY%" -m pip install --prefer-binary -r "%REQUIREMENTS%"
)

if errorlevel 1 (
  call :fail "Dependency installation failed." "Check network access or provide vendor\\wheels for offline install."
)

echo [INFO] Environment is ready.
if not exist "%PROJECT_ROOT%\.env" (
  echo [INFO] Optional cloud Agent config:
  echo        copy .env.example .env
)
echo [INFO] Next step:
echo        start_delivery.bat
exit /b 0

:detect_python
call :try_py_launcher 3.11
if not errorlevel 1 exit /b 0

call :try_py_launcher 3.10
if not errorlevel 1 exit /b 0

call :try_path_command python
if not errorlevel 1 exit /b 0

call :try_path_command python3
if not errorlevel 1 exit /b 0

call :try_python_exe "%LocalAppData%\Programs\Python\Python311\python.exe"
if not errorlevel 1 exit /b 0

call :try_python_exe "%LocalAppData%\Programs\Python\Python310\python.exe"
if not errorlevel 1 exit /b 0

call :try_python_exe "%ProgramFiles%\Python311\python.exe"
if not errorlevel 1 exit /b 0

call :try_python_exe "%ProgramFiles%\Python310\python.exe"
if not errorlevel 1 exit /b 0

call :try_python_exe "%ProgramFiles(x86)%\Python311\python.exe"
if not errorlevel 1 exit /b 0

call :try_python_exe "%ProgramFiles(x86)%\Python310\python.exe"
if not errorlevel 1 exit /b 0

call :fail "Python 3.10.x or 3.11.x was not found." "Install Python from python.org and enable Add Python to PATH."
exit /b 1

:try_py_launcher
where py >nul 2>nul
if errorlevel 1 exit /b 1

py -%~1 -c "import sys" >nul 2>nul
if errorlevel 1 exit /b 1

set "PY_CMD=py -%~1"
set "PY_LABEL=py -%~1"
exit /b 0

:try_path_command
where %~1 >nul 2>nul
if errorlevel 1 exit /b 1

%~1 -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3, 10), (3, 11)) else 1)" >nul 2>nul
if errorlevel 1 exit /b 1

set "PY_CMD=%~1"
set "PY_LABEL=%~1"
exit /b 0

:try_python_exe
if not exist "%~1" exit /b 1

"%~1" -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3, 10), (3, 11)) else 1)" >nul 2>nul
if errorlevel 1 exit /b 1

set "PY_CMD="%~1""
set "PY_LABEL=%~1"
exit /b 0

:fail
echo.
echo [ERROR] %~1
if not "%~2"=="" echo [ERROR] %~2
echo.
echo [INFO] Manual fallback commands:
echo        py -3.10 -m venv .venv
echo        .\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
echo        .\.venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
exit /b 1
