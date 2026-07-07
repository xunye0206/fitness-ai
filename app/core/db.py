"""异步数据库会话。

- DATABASE_URL 指向 Postgres(生产) 或 SQLite(本地/测试)；引擎按 URL 自动选 asyncpg/aiosqlite。
- 生产建议：Postgres + pgvector 扩展（向量检索）。本地/测试可用 SQLite，自动降级为 Python 余弦召回。
"""
import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from app.config import settings

logger = logging.getLogger("fitness_agent.db")


def _normalize_db_url(url: str) -> str:
    """补齐异步驱动后缀：postgresql:// → postgresql+asyncpg://。"""
    if url.startswith("postgresql://"):  # 缺驱动后缀
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    return url


_DATABASE_URL = _normalize_db_url(settings.database_url)
engine = create_async_engine(_DATABASE_URL, echo=False, future=True)
async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


def _is_postgres() -> bool:
    return _DATABASE_URL.startswith("postgresql+asyncpg://")


async def init_db() -> None:
    """建表 + (Postgres 时) 建 pgvector 扩展与 HNSW 索引。

    顺序：先建 vector 扩展 → 再 create_all（memory_embeddings 的 vector 列依赖该扩展）
    → 最后建 HNSW 索引加速余弦召回。SQLite 模式全部跳过。
    """
    async with engine.begin() as conn:
        if _is_postgres():
            try:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            except Exception as exc:
                logger.warning("pgvector 扩展创建失败（可能已存在或无权限）：%s", exc)
        await conn.run_sync(SQLModel.metadata.create_all)
        # 兼容旧库：diet_entries 缺 meal_type 列时补列（新库 create_all 已建，此处幂等跳过）
        await _migrate_diet_meal_type(conn)
        if _is_postgres():
            try:
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_memory_embeddings_vector "
                        "ON memory_embeddings USING hnsw (vector vector_cosine_ops)"
                    )
                )
            except Exception as exc:
                logger.warning("HNSW 索引创建失败（可忽略，召回仍可用但较慢）：%s", exc)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每个请求一个异步会话。"""
    async with async_session_factory() as session:
        yield session


async def _migrate_diet_meal_type(conn) -> None:
    """给已存在的 diet_entries 表补 meal_type 列（若不存在）。SQLite/Postgres 通用。"""
    try:
        def _has_col(sync_conn) -> bool:
            from sqlalchemy import inspect
            insp = inspect(sync_conn)
            if not insp.has_table("diet_entries"):
                return False
            cols = [c["name"] for c in insp.get_columns("diet_entries")]
            return "meal_type" in cols

        exists = await conn.run_sync(_has_col)
        if not exists:
            await conn.execute(
                text("ALTER TABLE diet_entries ADD COLUMN meal_type VARCHAR(20) DEFAULT 'other'")
            )
            logger.info("已为 diet_entries 表新增 meal_type 列")
    except Exception as exc:
        logger.warning("diet_entries.meal_type 迁移跳过：%s", exc)
