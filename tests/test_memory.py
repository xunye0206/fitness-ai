"""M5 记忆检索层测试：纯函数 + 注入 fake embed 的 index→recall 往返 + 禁用时优雅降级。

全程不联网、不依赖真实 API key（conftest 已把 EMBEDDING_PROVIDER 置空）。
"""
import asyncio

from app.core.db import async_session_factory
from app.llm.base import LLMResult
from app.modules.memory.service import (
    chunk_text,
    cosine_similarity,
    index_wiki,
    recall,
)

# 用固定词表做确定性伪 embedding：文本含某字计数作为该维分量，相同文本→相同向量。
# 词表需覆盖测试语料中的关键字符，否则查询向量全零、余弦为 0（与真实模型无关，仅测试用）。
VOCAB = list("健身饮食蛋白质训练睡眠蔬菜水果西兰花菠菜黄瓜番茄胸背腿")


def _vec(text: str) -> list[float]:
    return [float(text.count(ch)) for ch in VOCAB]


async def fake_embed(texts):
    vecs = [_vec(t) for t in texts]
    return LLMResult("", ok=True, raw={"vectors": vecs})


def test_chunk_text_short_returns_single():
    assert chunk_text("") == []
    assert chunk_text("短文本") == ["短文本"]


def test_chunk_text_long_splits_with_overlap():
    long = "健身" * 200  # 400 字 > 200
    chunks = chunk_text(long, max_chars=200, overlap=40)
    assert len(chunks) >= 2
    assert all(len(c) <= 200 for c in chunks)
    # 重叠区域应保证相邻块有交集（非完全断开）
    assert chunks[0][-40:] in chunks[1] or chunks[1].startswith(chunks[0][-40:])


def test_cosine_similarity_bounds():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0  # 零向量→0
    assert cosine_similarity([1.0], [2.0]) == 1.0


def test_index_and_recall_retrieves_relevant_chunk(client):
    async def run():
        async with async_session_factory() as session:
            # 三个不同主题来源，各产生一个块
            await index_wiki(
                session, 9901, "wiki/饮食偏好",
                "我讨厌吃西兰花和菠菜，蔬菜里只吃黄瓜和番茄。", embed=fake_embed,
            )
            await index_wiki(
                session, 9901, "wiki/训练偏好",
                "我每周练三次，主要练胸和背，不喜欢练腿。", embed=fake_embed,
            )
            await index_wiki(
                session, 9901, "wiki/睡眠",
                "我睡眠经常不足六小时，早上容易犯困。", embed=fake_embed,
            )
            hits = await recall(session, 9901, "我不爱吃西兰花", k=3, embed=fake_embed)
            assert len(hits) >= 1
            assert "西兰花" in hits[0].text          # 最相关块排第一
            assert hits[0].score > 0.3               # 相关性显著高于无关块（其他块为 0）

    asyncio.run(run())


def test_index_is_idempotent_same_source(client):
    async def run():
        async with async_session_factory() as session:
            from sqlmodel import select
            from app.modules.memory.domain import MemoryEmbedding

            await index_wiki(session, 9902, "wiki/偏好", "我讨厌西兰花。", embed=fake_embed)
            await index_wiki(session, 9902, "wiki/偏好", "我讨厌西兰花。", embed=fake_embed)
            rows = (
                await session.execute(
                    select(MemoryEmbedding).where(MemoryEmbedding.user_id == 9902)
                )
            ).scalars().all()
            assert len(rows) == 1  # 同来源重复索引不堆重复块

    asyncio.run(run())


def test_recall_graceful_when_embedding_disabled():
    async def run():
        async with async_session_factory() as session:
            # EMBEDDING_PROVIDER 在测试环境为空 → router.embed 返回 ok=False → recall 必须返回 []
            hits = await recall(session, 9903, "任意查询")
            assert hits == []

    asyncio.run(run())
