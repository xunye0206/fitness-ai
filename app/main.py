"""FastAPI 入口：装配模块路由、lifespan 初始化数据库。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.db import init_db
from app.core.logging import logger
from app.modules.auth.api import router as auth_router

logger = logging.getLogger("fitness_agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("数据库初始化完成")
    yield


app = FastAPI(title="健身AI Agent", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
