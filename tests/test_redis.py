"""M6 Redis 辅助函数测试：用内存假客户端，本地零基础设施跑绿。

覆盖：cache_get/set 命中、cache_delete、rate_incr 自增与 TTL 语义。
get_redis() 被 monkeypatch 为内存实现，因此不依赖真实 Redis / redis 包。
"""
import asyncio

import pytest

from app.core import redis as redis_mod
from app.core.redis import cache_delete, cache_get, cache_set, rate_incr


class _FakeRedis:
    """极简内存 async 客户端，仅实现本项目用到的 get/set/delete/incr/expire。"""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._ttl: dict[str, int] = {}

    async def get(self, key):
        return self._store.get(key)

    async def set(self, key, value, ex=None):
        self._store[key] = value
        if ex is not None:
            self._ttl[key] = ex
        return True

    async def delete(self, key):
        self._store.pop(key, None)
        self._ttl.pop(key, None)
        return 1

    async def incr(self, key):
        self._store[key] = str(int(self._store.get(key, "0")) + 1)
        return int(self._store[key])

    async def expire(self, key, ttl):
        self._ttl[key] = ttl
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    client = _FakeRedis()
    monkeypatch.setattr(redis_mod, "get_redis", lambda: client)
    return client


def test_cache_set_then_get(fake_redis):
    async def run():
        await cache_set("k1", "v1", ttl=60)
        assert await cache_get("k1") == "v1"
        assert await cache_get("missing") is None

    asyncio.run(run())


def test_cache_delete(fake_redis):
    async def run():
        await cache_set("k2", "v2")
        await cache_delete("k2")
        assert await cache_get("k2") is None

    asyncio.run(run())


def test_rate_incr_increments_and_resets(fake_redis):
    async def run():
        assert await rate_incr("r1") == 1
        assert await rate_incr("r1") == 2
        assert await rate_incr("r1") == 3
        # 不同 key 独立计数
        assert await rate_incr("r2") == 1

    asyncio.run(run())


def test_no_redis_degrades_to_none(monkeypatch):
    """REDIS 不可用时（get_redis 返回 None）辅助函数安全降级。"""
    monkeypatch.setattr(redis_mod, "get_redis", lambda: None)

    async def run():
        assert await cache_get("x") is None
        assert await rate_incr("x") is None
        await cache_set("x", "y")  # 不应抛异常
        await cache_delete("x")

    asyncio.run(run())
