"""记忆检索服务（service 层）。

职责：
- chunk_text：把一段 wiki 文本切成可嵌入的块（带重叠，避免切断语义）。
- index_wiki：把某来源文本分块→调 embedding→写入 memory_embeddings（先清旧再插新）。
- recall：把查询 embedding→与用户全部记忆块算余弦→返回 top-K。
- 全程优雅降级：embedding 不可用（未配置/网络错误）时，index 返回 False、recall 返回 []，
  绝不抛异常，保证主链路（饮食/训练/报告）不受影响。

设计要点：
- embed 默认走 app.llm.router.embed（与全站 LLM 调用同一入口，换供应商只改 .env）。
- embed 以可注入的函数形式传入（默认 router.embed），便于测试用 fake 不联网验证检索数学。
"""
import logging
import struct
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Sequence

from sqlalchemy import delete
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.llm.base import LLMResult
from app.modules.memory.domain import MemoryEmbedding, _is_postgres

logger = logging.getLogger("fitness_agent.memory")

# embed 函数签名：list[str] -> LLMResult，raw["vectors"] 为 list[list[float]]
EmbedFn = Callable[[list[str]], Awaitable[LLMResult]]


@dataclass
class RecallHit:
    """单条语义召回结果。"""
    text: str
    score: float
    source: str


# ---------- 向量打包/解包 ----------
def _pack_vec(vec: Sequence[float]) -> bytes:
    return struct.pack("<" + "f" * len(vec), *vec)


def _unpack_vec(b: bytes) -> list[float]:
    n = len(b) // 4
    if n == 0:
        return []
    return list(struct.unpack("<" + "f" * n, b))


def _to_vec(raw) -> list[float]:
    """把数据库里的 vector 值统一成 list[float]：bytes(打包) 解包，list 直接转。"""
    if isinstance(raw, (bytes, bytearray)):
        return _unpack_vec(bytes(raw))
    if raw is None:
        return []
    return [float(x) for x in raw]  # pgvector 返回 list[float]


# ---------- 纯函数（易测） ----------
def chunk_text(text: str, max_chars: int = 200, overlap: int = 40) -> list[str]:
    """把文本切成 <= max_chars 的块，块间保留 overlap 字符重叠。

    空文本返回 []；短于 max_chars 直接整段返回。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)  # 至少前进 1 字符，防死循环
    return chunks


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度，范围 [-1, 1]。任一为零向量返回 0.0。"""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _top_k(query_vec: list[float], rows: Sequence[MemoryEmbedding], k: int) -> list[RecallHit]:
    scored = []
    for r in rows:
        vec = _unpack_vec(r.vector)
        if not vec:
            continue
        scored.append((cosine_similarity(query_vec, vec), r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [RecallHit(text=r.text, score=s, source=r.source) for s, r in scored[:k]]


# ---------- 业务接口 ----------
async def index_wiki(
    session: AsyncSession,
    user_id: int,
    source: str,
    text: str,
    embed: EmbedFn | None = None,
) -> bool:
    """把某来源文本分块并写入向量索引。成功返回 True，embedding 失败返回 False（降级）。"""
    from app.llm import router  # 延迟导入，避免循环依赖

    embed_fn = embed or router.embed
    chunks = chunk_text(text)
    if not chunks:
        return False

    res = await embed_fn(chunks)
    if not res.ok or not res.raw or "vectors" not in res.raw:
        logger.warning("embedding 失败，跳过记忆索引：%s", res.error)
        return False
    vectors: list[list[float]] = res.raw["vectors"]
    if len(vectors) != len(chunks):
        logger.warning("embedding 返回条数(%d)与分块数(%d)不一致，跳过", len(vectors), len(chunks))
        return False

    model_name = get_settings().embedding_model or get_settings().embedding_provider
    try:
        # 同来源先清旧，再插新（幂等：重复索引不会堆重复块）
        await session.execute(
            delete(MemoryEmbedding).where(
                MemoryEmbedding.user_id == user_id, MemoryEmbedding.source == source
            )
        )
        for i, (ch, vec) in enumerate(zip(chunks, vectors)):
            # Postgres+pgvector 直接存 list[float]；SQLite 回退存打包 bytes
            vec_value = vec if _is_postgres() else _pack_vec(vec)
            session.add(
                MemoryEmbedding(
                    user_id=user_id,
                    source=source,
                    chunk_index=i,
                    text=ch,
                    vector=vec_value,
                    model=model_name,
                )
            )
        await session.commit()
        return True
    except Exception as exc:  # 落库异常也降级，不拖垮主链路
        logger.warning("记忆索引落库失败：%s", exc)
        await session.rollback()
        return False


async def recall(
    session: AsyncSession,
    user_id: int,
    query: str,
    k: int = 5,
    embed: EmbedFn | None = None,
) -> list[RecallHit]:
    """语义召回：把 query 向量化，返回该用户最相关的 k 条记忆。embedding 不可用返回 []。"""
    from app.llm import router  # 延迟导入，避免循环依赖

    embed_fn = embed or router.embed
    qres = await embed_fn([query])
    if not qres.ok or not qres.raw or "vectors" not in qres.raw:
        return []
    qvec: list[float] = qres.raw["vectors"][0]

    # Postgres + pgvector：DB 端按余弦距离排序取 top-K（HNSW 索引加速，量级大时显著快）
    if _is_postgres():
        try:
            rows = (
                await session.execute(
                    select(MemoryEmbedding)
                    .where(MemoryEmbedding.user_id == user_id)
                    .order_by(MemoryEmbedding.vector.cosine_distance(qvec))
                    .limit(k)
                )
            ).scalars().all()
        except Exception:
            return []
        return [
            RecallHit(text=r.text, score=cosine_similarity(qvec, _to_vec(r.vector)), source=r.source)
            for r in rows
        ]

    # SQLite 回退：全量取回，Python 算余弦取 top-K
    try:
        rows = (
            await session.execute(
                select(MemoryEmbedding).where(MemoryEmbedding.user_id == user_id)
            )
        ).scalars().all()
    except Exception:
        return []
    if not rows:
        return []
    return _top_k(qvec, rows, k)
