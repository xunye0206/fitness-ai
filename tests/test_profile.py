"""M10 长期画像记忆单元测试（零真实网络，注入 fake reason/embed）。

验证：
- extract_profile_memory 解析模型 JSON，只回非空三类；空类别不入。
- store_profile_memory 把三类分别写入 memory_embeddings（index_wiki 幂等）。
- update_profile_memory 端到端（自带 session），抽取为空时不写入；异常降级不抛。
- recall_profile 在 embedding 可用时召回画像，不可用时返回 []（降级）。

测试约定：所有被测函数均为 async，统一用 asyncio.run 包裹在同步测试里执行，
避免引入 anyio/asyncio 标记依赖；DB 相关的 session 与建表都在同一个 asyncio.run 内完成。
"""
import asyncio

import pytest
from sqlmodel import SQLModel

from app.agent.profile import (
    extract_profile_memory,
    recall_profile,
    store_profile_memory,
    update_profile_memory,
)
from app.llm.base import LLMResult, Message
from app.modules.memory.service import recall
from app.core.db import async_session_factory, engine


def _fake_reason(画像: str = "", 反思: str = "", 洞察: str = ""):
    async def _fn(messages: list[Message]) -> LLMResult:
        text = (
            '{"画像":"' + 画像 + '","反思":"' + 反思 + '","洞察":"' + 洞察 + '"}'
            if (画像 or 反思 or 洞察)
            else '{"画像":"","反思":"","洞察":""}'
        )
        return LLMResult(text=text, ok=True)

    return _fn


def _fake_embed():
    """返回与 fake 维度一致的向量（低维即可，cosine 不依赖真实维度）。"""
    dim = 8

    async def _fn(texts: list[str]) -> LLMResult:
        vectors = []
        for t in texts:
            base = [0.0] * dim
            for ch in t:
                base[ord(ch) % dim] += 1.0
            vectors.append(base)
        return LLMResult(text="", ok=True, raw={"vectors": vectors})

    return _fn


async def _make_session():
    """建表并返回一个新的 async session（在同 loop 内操作，避免跨 loop 绑定问题）。"""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return async_session_factory()


def test_extract_returns_only_nonempty_categories():
    fn = _fake_reason(画像="目标是减脂", 反思="讨厌西兰花", 洞察="")
    out = asyncio.run(extract_profile_memory("我想减脂", "好的，控制热量", reason_fn=fn))
    assert out == {"画像": "目标是减脂", "反思": "讨厌西兰花"}
    assert "洞察" not in out


def test_extract_empty_when_no_new_info():
    fn = _fake_reason()
    out = asyncio.run(extract_profile_memory("今天天气不错", "嗯", reason_fn=fn))
    assert out == {}


def test_extract_degrades_on_llm_error():
    async def _boom(messages):
        raise RuntimeError("llm down")

    out = asyncio.run(extract_profile_memory("x", "y", reason_fn=_boom))
    assert out == {}


def test_store_writes_three_sources():
    async def _run():
        session = await _make_session()
        try:
            profiles = {"画像": "目标增肌", "反思": "熬夜必练崩", "洞察": "下肢偏弱"}
            await store_profile_memory(session, 7701, profiles, embed_fn=_fake_embed())
            hits = await recall(session, 7701, "训练计划怎么安排", k=5, embed=_fake_embed())
            texts = {h.text for h in hits}
            assert "目标增肌" in texts
            assert "熬夜必练崩" in texts
            assert "下肢偏弱" in texts
        finally:
            await session.close()

    asyncio.run(_run())


def test_update_end_to_end_writes():
    async def _run():
        session = await _make_session()
        try:
            fn = _fake_reason(画像="上班族久坐", 反思="", 洞察="")
            # 传 session 复用，避免 fire-and-forget 在测试里跨 loop
            await update_profile_memory(
                7702, "我久坐腰酸", "注意拉伸", session=session, reason_fn=fn, embed_fn=_fake_embed()
            )
            hits = await recall(session, 7702, "久坐怎么练", k=5, embed=_fake_embed())
            assert any("久坐" in h.text for h in hits)
        finally:
            await session.close()

    asyncio.run(_run())


def test_update_no_write_when_extract_empty():
    async def _run():
        session = await _make_session()
        try:
            fn = _fake_reason()
            await update_profile_memory(
                7703, "hi", "hi", session=session, reason_fn=fn, embed_fn=_fake_embed()
            )
            hits = await recall(session, 7703, "anything", k=5, embed=_fake_embed())
            assert hits == []  # 没写东西，召回为空
        finally:
            await session.close()

    asyncio.run(_run())


def test_recall_profile_degrades_without_embedding():
    async def _run():
        session = await _make_session()
        try:

            async def _bad_embed(texts):
                return LLMResult(text="", ok=True, raw={})  # 缺 vectors → 降级

            hits = await recall_profile(session, 7704, "今天练什么", k=3, embed_fn=_bad_embed)
            assert hits == []
        finally:
            await session.close()

    asyncio.run(_run())
