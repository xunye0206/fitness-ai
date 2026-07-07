"""memory 模块数据模型（domain 层）。

一张表存全部用户的记忆向量块，向量以 float32 打包成 bytes 存 BLOB，
检索时用 Python 算余弦相似度取 top-K。不引入 Postgres/Redis，贴合
"数值只进 SQLite、零运维"的存储分层约定。
"""
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryEmbedding(SQLModel, table=True):
    __tablename__ = "memory_embeddings"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    source: str = ""          # 来源标识，如 wiki 页名 "wiki/饮食偏好"
    chunk_index: int = 0      # 同一来源内的分块序号
    text: str = ""            # 分块原文（召回时回喂给 LLM 上下文）
    vector: bytes = Field(default=b"")   # float32 打包向量（struct 小端）
    model: str = ""           # 生成该向量所用的 embedding 模型名
    created_at: datetime = Field(default_factory=_now)
