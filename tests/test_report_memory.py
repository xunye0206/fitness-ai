"""M9+ 向量库闭环测试：报告生成后写入向量库，且可被 recall 召回（跨周期记忆）。

fake provider 的 embed 不返回 vectors，会被 index_wiki/recall 降级跳过，
故这里用 monkeypatch 注入一个确定性的可控 embed，专门验证「写入→召回」机制本身。
"""
import asyncio

from sqlmodel import select

from app.core.db import async_session_factory
from app.llm.base import LLMResult
from app.modules.memory.domain import MemoryEmbedding
from app.modules.memory.service import recall
from app.modules.report.service import generate_report


def _auth(client):
    client.post("/auth/register", json={"username": "vectester", "password": "p123456"})
    tok = client.post("/auth/login", json={"username": "vectester", "password": "p123456"}).json()["access_token"]
    uid = client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()["id"]
    return tok, uid


async def _fake_embed(texts):
    """确定性 embed：文本字符 ord 和的低 8 位展开成 dim=8 向量。必须是 async（调用处 await）。"""
    vecs = []
    for t in texts:
        h = sum(ord(c) for c in t)
        vecs.append([float((h >> i) & 1) for i in range(8)])
    return LLMResult(text="", ok=True, raw={"vectors": vecs})


def test_generate_report_writes_vector_memory(client, monkeypatch):
    monkeypatch.setattr("app.llm.router.embed", _fake_embed)
    tok, uid = _auth(client)

    async def run():
        async with async_session_factory() as s:
            rep = await generate_report(s, uid, days=7)
            rows = (await s.execute(
                select(MemoryEmbedding).where(MemoryEmbedding.user_id == uid)
            )).scalars().all()
            return rep, rows

    rep, rows = asyncio.run(run())
    assert rep.id is not None
    assert len(rows) >= 1, "报告生成后应写入向量库"
    assert rows[0].source.startswith("report:")


def test_report_memory_recallable(client, monkeypatch):
    """写入后，recall 能以同用户身份召回该报告（跨周期记忆可用）。"""
    monkeypatch.setattr("app.llm.router.embed", _fake_embed)
    tok, uid = _auth(client)

    async def run_write():
        async with async_session_factory() as s:
            return await generate_report(s, uid, days=7)

    rep = asyncio.run(run_write())

    async def run_recall():
        async with async_session_factory() as s:
            return await recall(s, uid, rep.summary, k=5)

    hits = asyncio.run(run_recall())
    assert len(hits) >= 1, "应能从向量库召回刚写入的报告"
    assert hits[0].source.startswith("report:")
