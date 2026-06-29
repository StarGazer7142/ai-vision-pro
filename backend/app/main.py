import logging
import os
import asyncio
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.routes import router
from backend.app.core.logging import configure_logging
from backend.app.services.rules_engine import engine
from backend.app.services import tracking_service
from backend.app.services.yolo_service import yolo_service

configure_logging()
logger = logging.getLogger("ai-platform")


async def daily_reset_task():
    while True:
        now = datetime.now()
        target = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        logger.info(f"定时任务已启动：将在 {wait_seconds:.0f} 秒后（凌晨3点）执行系统深度重置。")
        await asyncio.sleep(wait_seconds)
        logger.info("开始执行凌晨定时清理任务...")
        try:
            engine.reset_states()
            if hasattr(tracking_service, 'reset'):
                tracking_service.reset()
            logger.info("✅ 凌晨定时清理任务执行完成！")
        except Exception as e:
            logger.error(f"❌ 凌晨定时清理失败: {e}")


@asynccontextmanager
async def lifespan(app):
    logger.info("AI platform started. revision=%s", engine.config_revision)
    try:
        yolo_service.load()
        logger.info("YOLO 模型预热完成")
    except Exception as e:
        logger.warning("YOLO 模型预热失败（首次请求时重试）: %s", e)
    asyncio.create_task(daily_reset_task())
    yield


app = FastAPI(title="AI视频识别信号平台", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:80",
        "http://localhost:80",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# 简易速率限制: 每个IP每60秒最多120次请求
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_MAX = 120
RATE_LIMIT_WINDOW = 60.0

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    # 清理过期记录
    _rate_limit_store[client_ip] = [t for t in _rate_limit_store[client_ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_MAX:
        return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})
    _rate_limit_store[client_ip].append(now)
    return await call_next(request)

os.makedirs("data/outputs", exist_ok=True)
app.mount("/download", StaticFiles(directory="data/outputs"), name="outputs")

app.include_router(router)


# --- 手动注册 reset-counts 路由（绕过路由注册问题）---
from fastapi import Header
from typing import Optional

@app.post("/ops/reset-counts")
def _reset_counts(authorization: Optional[str] = Header(default=None)):
    from backend.app.api.routes import _require_roles, _operator_name
    from backend.app.services.storage_service import storage_service
    _, operator = _require_roles(authorization, {"super_admin", "admin"})
    result = engine.reset_cumulative_counts()
    storage_service.record_operation(
        module="ops",
        action="reset_counts",
        operator=operator["username"],
        target="cumulative_counts",
        detail=result,
    )
    return {"ok": True, "data": result}
