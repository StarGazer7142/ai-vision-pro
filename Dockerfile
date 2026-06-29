FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    PYTHONPATH=/app

WORKDIR /app

# ── apt 换源（阿里云 Debian 镜像）─────────────────────────────
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true

# ── 系统依赖：Python 依赖 + Nginx ───────────────────────────
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg libgl1 libglib2.0-0 nginx \
    && rm -rf /var/lib/apt/lists/*

# ── Python 依赖（排除 torch，阿里云源）───────────────────────
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir \
    --default-timeout=1000 --retries 5 \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    $(grep -vE '^\s*(torch|torchvision)' requirements.txt)

# ── PyTorch（阿里云源）───────────────────────────────────────
RUN pip install --no-cache-dir \
    --default-timeout=1000 --retries 5 \
    torch torchvision \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com

# ── 后端代码 ────────────────────────────────────────────────
COPY backend /app/backend

# ── 配置 + 模型 + 脚本 ──────────────────────────────────────
COPY config /app/config
COPY models /app/models
COPY scripts /app/scripts
COPY .env.example /app/.env

# ── 前端静态文件 ────────────────────────────────────────────
COPY frontend/static /usr/share/nginx/html

# ── Nginx 配置（删除默认站点，加载我们的配置）────────────────
RUN rm -f /etc/nginx/sites-enabled/default
COPY nginx.conf /etc/nginx/conf.d/default.conf

# ── 运行时目录 ──────────────────────────────────────────────
RUN mkdir -p /app/data/runtime/logs /app/data/backups /app/data/outputs \
    /app/data/replay /app/data/replay_clips /app/data/uploads/videos

# ── 启动脚本：Nginx（前台）+ FastAPI（后台）──────────────
RUN printf '#!/bin/bash\nnginx -g "daemon off;" &\nexec python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000\n' \
    > /app/start.sh && chmod +x /app/start.sh

EXPOSE 80

CMD ["/app/start.sh"]
