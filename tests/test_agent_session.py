"""SessionManager 单元测试（内存存储，零网络）。

验证：追加/读取历史、跨管理器共享同一存储、超 token 预算触发压缩（summary 指针）、
未超预算不压缩。
"""
import asyncio

from app.agent.session import InMemorySessionStore, SessionManager, StoredMessage


async def _append(mgr, sid, msg):
    await mgr.append(sid, msg)


async def _get(mgr, sid):
    return await mgr.get_history(sid)


async def _compact(mgr, sid):
    return await mgr.compact_if_needed(sid)


def test_append_and_get():
    store = InMemorySessionStore()
    mgr = SessionManager(store)
    asyncio.run(_append(mgr, "s1", StoredMessage(role="user", content="hi")))
    asyncio.run(_append(mgr, "s1", StoredMessage(role="assistant", content="hello")))
    hist = asyncio.run(_get(mgr, "s1"))
    assert [m.content for m in hist] == ["hi", "hello"]


def test_history_shared_across_managers_same_store():
    store = InMemorySessionStore()
    asyncio.run(_append(SessionManager(store), "s2", StoredMessage(role="user", content="a")))
    hist = asyncio.run(_get(SessionManager(store), "s2"))
    assert hist[0].content == "a"


def test_compaction_triggers_when_over_budget():
    store = InMemorySessionStore()
    calls = {"n": 0}

    async def fake_summarizer(messages):
        calls["n"] += 1
        return "摘要"

    mgr = SessionManager(store, max_tokens=10, summarizer=fake_summarizer)
    for _ in range(5):
        asyncio.run(_append(mgr, "s3", StoredMessage(role="user", content="这是很长的一句用来占 token 的话 " * 5)))
    compacted = asyncio.run(_compact(mgr, "s3"))
    assert compacted is True
    assert calls["n"] == 1
    hist = asyncio.run(_get(mgr, "s3"))
    assert len(hist) == 1
    assert hist[0].role == "system"
    assert hist[0].content == "摘要"


def test_no_compaction_under_budget():
    store = InMemorySessionStore()

    async def fake_summarizer(messages):
        return "x"

    mgr = SessionManager(store, max_tokens=100000, summarizer=fake_summarizer)
    asyncio.run(_append(mgr, "s4", StoredMessage(role="user", content="短")))
    assert asyncio.run(_compact(mgr, "s4")) is False
