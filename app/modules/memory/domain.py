"""memory 模块数据模型（domain 层）。

一张表存全部用户的记忆向量块。向量存储按数据库后端自适应：
- Postgres + pgvector：vector 列用 Vector(EMBEDDING_DIM) 类型，HNSW 索引加速余弦召回。
- SQLite 回退（本地/测试）：向量以 float32 打包成 bytes 存 BLOB，召回用 Python 算余弦。
两种形态对业务层透明，recall() 统一返回 top-K。
"""
import logging
import sqlalchemy as sa
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel

from app.config import get_settings

logger = logging.getLogger("fitness_agent.memory")


def _is_postgres() -> bool:
    url = get_settings().database_url
    return url.startswith(("postgresql://", "postgresql+asyncpg://", "postgres://"))


def _make_vector_field():
    """按后端返回合适的 vector 列定义。"""
    if _is_postgres():
        try:
            from pgvector.sqlalchemy import Vector

            return Field(sa_column=sa.Column(Vector(get_settings().embedding_dim)))
        except ImportError:
            logger.warning(
                "DATABASE_URL 为 Postgres 但 pgvector 未安装；退回 bytes 存储（生产不可用，请 pip install pgvector）"
            )
    # SQLite / 回退：float32 打包 bytes
    return Field(default=b"", sa_type=sa.LargeBinary)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryEmbedding(SQLModel, table=True):
    __tablename__ = "memory_embeddings"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    source: str = ""          # 来源标识，如 wiki 页名 "wiki/饮食偏好"
    chunk_index: int = 0      # 同一来源内的分块序号
    text: str = ""            # 分块原文（召回时回喂给 LLM 上下文）
    vector: bytes = _make_vector_field()   # Postgres 下运行时存 list[float]，SQLite 下存打包 bytes
    model: str = ""           # 生成该向量所用的 embedding 模型名
    created_at: datetime = Field(default_factory=_now)
