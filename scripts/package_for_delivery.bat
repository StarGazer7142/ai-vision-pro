@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "SRC=%CD%"
set "PKG=%SRC%\..\AI-VISION_PRO_v0.3.0_Package"
set "ZIP=%SRC%\..\AI-VISION_PRO_v0.3.0_Package.zip"

echo ============================================
echo   AI-VISION PRO v0.3.0 项目打包脚本
echo ============================================
echo.

:: 清理旧的打包目录
if exist "%PKG%" rmdir /s /q "%PKG%"
if exist "%ZIP%" del /f /q "%ZIP%"

echo [1/6] 创建打包目录结构...
mkdir "%PKG%"                           2>nul
mkdir "%PKG%\backend"                   2>nul
mkdir "%PKG%\backend\app"               2>nul
mkdir "%PKG%\backend\app\api"           2>nul
mkdir "%PKG%\backend\app\core"          2>nul
mkdir "%PKG%\backend\app\schemas"       2>nul
mkdir "%PKG%\backend\app\services"      2>nul
mkdir "%PKG%\frontend\static"           2>nul
mkdir "%PKG%\config"                    2>nul
mkdir "%PKG%\models"                    2>nul
mkdir "%PKG%\scripts"                   2>nul
mkdir "%PKG%\docs"                      2>nul
mkdir "%PKG%\data\acceptance_demo"      2>nul
mkdir "%PKG%\data\runtime"              2>nul
echo    OK

echo [2/6] 复制后端源码...
xcopy /s /e /q /y "%SRC%\backend\app\*.py"           "%PKG%\backend\app\"         >nul
xcopy /s /e /q /y "%SRC%\backend\app\api\*.py"       "%PKG%\backend\app\api\"     >nul
xcopy /s /e /q /y "%SRC%\backend\app\core\*.py"      "%PKG%\backend\app\core\"    >nul
xcopy /s /e /q /y "%SRC%\backend\app\schemas\*.py"   "%PKG%\backend\app\schemas\" >nul
xcopy /s /e /q /y "%SRC%\backend\app\services\*.py"  "%PKG%\backend\app\services\" >nul
echo    OK

echo [3/6] 复制前端、配置、模型、脚本、文档...
xcopy /s /e /q /y "%SRC%\frontend\static\*"          "%PKG%\frontend\static\"     >nul
xcopy /q /y       "%SRC%\config\*"                    "%PKG%\config\"              >nul
xcopy /q /y       "%SRC%\models\*"                    "%PKG%\models\"              >nul
xcopy /s /e /q /y "%SRC%\scripts\*.py"               "%PKG%\scripts\"             >nul
xcopy /q /y       "%SRC%\docs\*.docx"                "%PKG%\docs\"                >nul
xcopy /q /y       "%SRC%\docs\*.md"                  "%PKG%\docs\"                >nul
echo    OK

echo [4/6] 复制启动脚本和配置文件...
copy /y "%SRC%\setup_env.bat"              "%PKG%\" >nul
copy /y "%SRC%\start_all_dev.bat"          "%PKG%\" >nul
copy /y "%SRC%\start_backend_dev.bat"      "%PKG%\" >nul
copy /y "%SRC%\start_frontend.bat"         "%PKG%\" >nul
copy /y "%SRC%\start_delivery.bat"         "%PKG%\" >nul
copy /y "%SRC%\start_webcam_demo.bat"      "%PKG%\" >nul
copy /y "%SRC%\requirements.txt"           "%PKG%\" >nul
copy /y "%SRC%\.env.example"               "%PKG%\" >nul
copy /y "%SRC%\.gitignore"                 "%PKG%\" >nul
copy /y "%SRC%\README.md"                  "%PKG%\" >nul
copy /y "%SRC%\README_PORTABLE.md"         "%PKG%\" >nul
copy /y "%SRC%\Dockerfile"                 "%PKG%\" >nul
copy /y "%SRC%\docker-compose.yml"         "%PKG%\" >nul
echo    OK

echo [5/6] 复制运行时数据目录...
if exist "%SRC%\data\acceptance_demo\*" xcopy /s /e /q /y "%SRC%\data\acceptance_demo\*" "%PKG%\data\acceptance_demo\" >nul
copy /y "%SRC%\data\dataset.yaml"             "%PKG%\data\" >nul
copy /y "%SRC%\data\public_data_plan.md"      "%PKG%\data\" >nul
echo    OK

echo [6/6] 生成便携版使用说明...
(
echo # AI-VISION PRO v0.3.0 - 便携版
echo.
echo ## 快速开始
echo.
echo ### 1. 环境准备（首次）
echo.
echo ```powershell
echo cd %PKG_NAME%
echo .\setup_env.bat
echo ```
echo.
echo ### 2. 启动项目
echo.
echo ```powershell
echo .\start_all_dev.bat
echo ```
echo.
echo ### 3. 打开浏览器访问
echo.
echo - 首页: http://127.0.0.1:5500/index.html
echo - 后端: http://127.0.0.1:8000/health
echo.
echo ## 配置 API Key（可选）
echo.
echo 复制 .env.example 为 .env，填写以下字段：
echo.
echo ```env
echo API_KEY=你的DeepSeek密钥
echo MIMO_API_KEY=你的MiMo密钥
echo ```
echo.
echo ## 系统要求
echo.
echo - Python 3.10 或 3.11（64位）
echo - Windows 10/11
echo - 建议 8GB 内存
echo - 可选: ffmpeg（用于视频裁剪）
echo.
echo ## 目录说明
echo.
echo ```
echo backend/       - 后端源码（FastAPI + YOLO + 规则引擎）
echo frontend/      - 前端静态页面
echo config/        - 规则/跟踪器/视觉后端配置
echo models/        - YOLO 模型权重文件
echo scripts/       - 训练/推理/运维脚本
echo docs/          - 项目文档（需求/设计/测试/用户手册）
echo data/          - 运行时数据（数据库、视频等自动生成）
echo .env.example   - 环境变量模板
echo setup_env.bat  - 首次环境安装脚本
echo start_all_dev.bat - 一键启动脚本
echo ```
) > "%PKG%\QUICK_START.md"
echo    OK

echo.
echo ============================================
echo   打包目录创建完成
echo   位置: %PKG%
echo ============================================
echo.

:: 计算打包目录大小
for /f "tokens=3" %%a in ('dir /s "%PKG%" ^| findstr /c:"个文件"') do set "FILE_COUNT=%%a"
for /f "tokens=3" %%a in ('dir /s "%PKG%" ^| findstr /c:"个目录"') do set "DIR_COUNT=%%a"
echo 文件数: %FILE_COUNT%
echo 目录数: %DIR_COUNT%
echo.

echo 正在创建 ZIP 压缩包...
powershell -NoProfile -ExecutionPolicy Bypass -Command "
    $src = '%PKG%'
    $zip = '%ZIP%'
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path $src -DestinationPath $zip -CompressionLevel Optimal
    $size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Write-Host \"ZIP 压缩包创建完成: $zip\" -ForegroundColor Green
    Write-Host \"文件大小: $size MB\" -ForegroundColor Cyan
"

echo.
echo ============================================
echo   打包完成！
echo   ZIP 文件: %ZIP%
echo ============================================
echo.
pause
