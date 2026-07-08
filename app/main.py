"""FastAPI 入口：装配模块路由、lifespan 初始化数据库。"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.db import init_db
from app.core.logging import logger
from app.modules.memory import domain as _memory_domain  # noqa: F401  确保记忆表被建出
from app.modules.auth.api import router as auth_router
from app.modules.diet.api import router as diet_router
from app.modules.training.api import router as training_router
from app.modules.report.api import router as report_router
from app.modules.push.api import router as push_router
from app.modules.push.scheduler import register_scheduler, shutdown_scheduler
from app.agent.api import router as agent_router

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

# 验证页从 file:// 预览窗跨源访问时需要 CORS（本地开发，允许全部来源）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(diet_router)
app.include_router(training_router)
app.include_router(report_router)
app.include_router(push_router)
app.include_router(agent_router)

# 验证页（静态 HTML）
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/privacy")
async def privacy_policy():
    """隐私政策页（合规：公网收集健康/饮食数据须明示政策）。"""
    return FileResponse("static/privacy.html")
