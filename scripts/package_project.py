# -*- coding: utf-8 -*-
"""AI-VISION PRO 项目打包脚本"""
import os, shutil, zipfile
from pathlib import Path

SRC = Path(r'D:\Project')
OUT = Path(r'D:\AI-VISION_PRO_v0.3.0_Package')
ZIP_PATH = Path(r'D:\AI-VISION_PRO_v0.3.0_Package.zip')

# 清理旧目录
if OUT.exists():
    shutil.rmtree(OUT)
if ZIP_PATH.exists():
    ZIP_PATH.unlink()

OUT.mkdir(parents=True, exist_ok=True)

# === 要复制的文件 ===
INCLUDE_FILES = [
    'setup_env.bat', 'start_all_dev.bat', 'start_backend_dev.bat',
    'start_frontend.bat', 'start_delivery.bat', 'start_webcam_demo.bat',
    'requirements.txt', '.env.example', '.gitignore',
    'README.md', 'README_PORTABLE.md', 'Dockerfile', 'docker-compose.yml',
]

# === 要复制的目录 ===
INCLUDE_DIRS = [
    'backend', 'frontend', 'config', 'models', 'scripts', 'docs',
]

# === data 目录要复制的内容 ===
INCLUDE_DATA_FILES = [
    'data/dataset.yaml',
    'data/public_data_plan.md',
]
INCLUDE_DATA_DIRS = [
    'data/acceptance_demo',
]

# === 排除的目录/文件模式 ===
EXCLUDE_DIRS = {'__pycache__', '.venv', '.idea', '.vscode', '.claude', 'node_modules'}
EXCLUDE_EXTS = {'.pyc', '.pyo'}

def should_skip(path: Path) -> bool:
    """判断是否跳过该路径"""
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return True
    if path.suffix in EXCLUDE_EXTS:
        return True
    return False

# === 1. 复制文件 ===
print('[1/4] 复制配置文件和启动脚本...')
for f in INCLUDE_FILES:
    src = SRC / f
    if src.exists():
        shutil.copy2(src, OUT / f)
        print(f'  + {f}')

# === 2. 复制目录 ===
print('[2/4] 复制源码和资源目录...')
for dirname in INCLUDE_DIRS:
    src = SRC / dirname
    dst = OUT / dirname
    if src.exists():
        def ignore_fn(directory, files):
            skipped = []
            for f in files:
                fp = Path(directory) / f
                if f in EXCLUDE_DIRS or (fp.is_file() and fp.suffix in EXCLUDE_EXTS):
                    skipped.append(f)
            return skipped
        shutil.copytree(src, dst, ignore=ignore_fn)
        # 统计文件数
        count = sum(1 for _ in dst.rglob('*') if _.is_file())
        print(f'  + {dirname}/ ({count} files)')

# === 3. 复制 data 目录部分内容 ===
print('[3/4] 复制运行时数据...')
data_dst = OUT / 'data'
data_dst.mkdir(exist_ok=True)
for f in INCLUDE_DATA_FILES:
    src = SRC / f
    if src.exists():
        dst_file = data_dst / Path(f).name
        shutil.copy2(src, dst_file)
        print(f'  + {f}')
for d in INCLUDE_DATA_DIRS:
    src = SRC / d
    if src.exists():
        shutil.copytree(src, data_dst / Path(d).name)
        print(f'  + {d}/')

# === 4. 生成 QUICK_START.md ===
print('[4/4] 生成使用说明...')
quick_start = r"""# AI-VISION PRO v0.3.0 - 便携版

## 快速开始

### 1. 解压到任意目录（路径不要含中文）

例如解压到 `D:\AI_Vision_Project`

### 2. 首次环境安装

```powershell
cd D:\AI_Vision_Project
.\setup_env.bat
```

### 3. 启动项目

```powershell
.\start_all_dev.bat
```

### 4. 打开浏览器访问

- 首页: http://127.0.0.1:5500/index.html
- 后端健康检查: http://127.0.0.1:8000/health

## 配置 API Key（可选）

复制 `.env.example` 为 `.env`，填写以下字段：

```
API_KEY=你的DeepSeek密钥
MIMO_API_KEY=你的MiMo密钥
```

## 系统要求

- Python 3.10 或 3.11（64位）
- Windows 10/11
- 建议 8GB 内存
- 可选: ffmpeg（用于视频裁剪功能）

## 目录说明

```
backend/           后端源码（FastAPI + YOLO + 规则引擎）
frontend/          前端静态页面（HTML/CSS/JS + ECharts）
config/            规则/跟踪器/视觉后端配置文件
models/            YOLO 模型权重文件（yolo26s.pt）
scripts/           训练/推理/运维脚本
docs/              项目文档（需求/设计/测试/用户手册/总结报告）
data/              运行时数据目录（数据库等自动生成）
.env.example       环境变量模板
setup_env.bat      首次环境安装脚本（创建 .venv + 安装依赖）
start_all_dev.bat  一键启动脚本（后端 + 前端）
```

## 注意事项

1. 首次运行 `setup_env.bat` 需要联网下载 Python 依赖
2. 如果没有摄像头，可以在页面上传视频文件进行测试
3. 模型文件 `models/yolo26s.pt` 约 50MB，是检测功能必需的
4. 数据库文件会自动在 `data/runtime/` 下创建
"""
(OUT / 'QUICK_START.md').write_text(quick_start, encoding='utf-8')
print('  + QUICK_START.md')

# === 创建 ZIP ===
print('')
print('正在创建 ZIP 压缩包...')
with zipfile.ZipFile(ZIP_PATH, 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(OUT):
        # 跳过缓存目录
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if Path(f).suffix in EXCLUDE_EXTS:
                continue
            fp = Path(root) / f
            arcname = fp.relative_to(OUT.parent)
            zf.write(fp, arcname)

zip_size = ZIP_PATH.stat().st_size / (1024 * 1024)
print('')
print('=' * 50)
print(f'  打包完成！')
print(f'  ZIP: {ZIP_PATH}')
print(f'  大小: {zip_size:.1f} MB')
print('=' * 50)
