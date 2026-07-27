"""P2 单元测试：SQLite 持久化会话存储 + 超预算压缩。

沿用 conftest 的 client 夹具（自动 create_all，含 agent_session_messages 表）。
压缩用「假 summarizer」避免烧真 LLM；只验压缩触发条件与结果。
"""
import asyncio

from app.agent.session import SessionManager, SqliteSessionStore, StoredMessage


def test_sqlite_store_roundtrip(client):
    async def run():
        store = SqliteSessionStore()
        await store.save(
            "s1",
            [
                StoredMessage(role="user", content="hi"),
                StoredMessage(
                    role="assistant",
                    content="hello",
                    tool_calls=[{"id": "c1", "name": "t"}],
                    tool_call_id="c1",
                    name="t",
                ),
            ],
        )
        loaded = await store.load("s1")
        assert len(loaded) == 2
        assert loaded[0].content == "hi"
        assert loaded[1].tool_call_id == "c1"
        assert loaded[1].tool_calls == [{"id": "c1", "name": "t"}]

    asyncio.run(run())


def test_compaction_triggers_with_summarizer(client):
    async def run():
        store = SqliteSessionStore()

        async def fake_summary(msgs):
            return "SUMMARY"

        mgr = SessionManager(store, max_tokens=10, summarizer=fake_summary)
        # 塞入超预算历史（≈50 tokens > 10）
        await store.save("s2", [StoredMessage(role="user", content="x" * 100)])
        did = await mgr.compact_if_needed("s2")
        assert did is True
        loaded = await store.load("s2")
        assert len(loaded) == 1 and loaded[0].content == "SUMMARY"

    asyncio.run(run())


def test_no_compaction_under_budget(client):
    async def run():
        store = SqliteSessionStore()

        async def fake_summary(msgs):
            return "S"

        mgr = SessionManager(store, max_tokens=10000, summarizer=fake_summary)
        await store.save("s3", [StoredMessage(role="user", content="hi")])
        assert await mgr.compact_if_needed("s3") is False

    asyncio.run(run())
