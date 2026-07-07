"""FastAPI 入口：装配模块路由、lifespan 初始化数据库。"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.core.db import init_db
from app.core.logging import logger
from app.modules.auth.api import router as auth_router
from app.modules.diet.api import router as diet_router
from app.modules.training.api import router as training_router
from app.modules.report.api import router as report_router
from app.modules.push.api import router as push_router
from app.modules.push.scheduler import register_scheduler, shutdown_scheduler

logger = logging.getLogger("fitness_agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.upload_dir, exist_ok=True)
    await init_db()
    logger.info("数据库初始化完成")
    register_scheduler()  # M4：启动每日固定 + 事件巡检推送
    try:
        yield
    finally:
        shutdown_scheduler()
        logger.info("推送调度已关闭")


app = FastAPI(title="健身AI Agent", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(diet_router)
app.include_router(training_router)
app.include_router(report_router)
app.include_router(push_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
