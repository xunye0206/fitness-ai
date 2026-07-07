"""Redis 客户端单例 + 进程内内存兜底（优雅降级）。

用途：
- agent 上下文热缓存（build_context 命中即返，避免每次重算 7 天聚合）。
- 推送 ≤3/天 限流计数。

降级策略（关键变更）：
- REDIS_URL 为空 / redis 包未装 / 连不上 → 自动降级为【进程内内存缓存】
  （带 TTL，单进程内可命中），不再是「无缓存、永不命中」。
- 多进程 / 多机部署建议配置 REDIS_URL，以跨进程共享缓存与限流计数。
- 内存缓存与 Redis 异常都绝不抛异常、不拖垮主链路（饮食/训练/报告/推送）。
"""
import logging
import time
from functools import lru_cache
from typing import Optional

from app.config import settings

logger = logging.getLogger("fitness_agent.redis")

try:
    import redis.asyncio as aioredis

    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False


class _MemCache:
    """极简 TTL 内存缓存，方法全 async 以对齐 Redis 客户端接口。

    key -> (value:str, expire_ts:float)；expire_ts<=0 表示永不过期。
    单进程内有效；多进程不共享（那是 Redis 的职责）。
    """

    def __init__(self):
        self._store: dict[str, tuple[str, float]] = {}

    async def get(self, key: str) -> Optional[str]:
        item = self._store.get(key)
        if item is None:
            return None
        val, exp = item
        if exp and time.time() > exp:
            self._store.pop(key, None)
            return None
        return val

    async def set(self, key: str, value: str, ttl: int = 600) -> None:
        exp = time.time() + ttl if ttl and ttl > 0 else 0.0
        self._store[key] = (value, exp)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def incr(self, key: str, ttl: int = 86400) -> int:
        item = self._store.get(key)
        if item is not None:
            _, exp = item
            if exp and time.time() > exp:
                item = None
                self._store.pop(key, None)
        # value 非数字（如与 cache 同 key 被写入字符串）时重置为 1，不抛异常
        cur = 0
        if item is not None:
            try:
                cur = int(item[0])
            except (ValueError, TypeError):
                cur = 0
        n = cur + 1
        exp = time.time() + ttl if ttl and ttl > 0 else 0.0
        self._store[key] = (str(n), exp)
        return n


_mem_cache = _MemCache()


@lru_cache
def get_redis():
    """返回 redis.asyncio 客户端；不可用时返回 None（调用方降级到内存）。"""
    if not settings.redis_url or not _HAS_REDIS:
        return None
    try:
        return aioredis.from_url(
            settings.redis_url, decode_responses=True, socket_connect_timeout=2
        )
    except Exception as exc:
        logger.warning("Redis 客户端创建失败，已降级为内存缓存：%s", exc)
        return None


def _backend():
    """当前缓存后端：Redis 客户端优先，否则进程内内存兜底。"""
    r = get_redis()
    return r if r is not None else _mem_cache


async def cache_get(key: str) -> Optional[str]:
    """读取缓存；Redis 或内存兜底，任一可用即返回。"""
    try:
        return await _backend().get(key)
    except Exception:
        try:
            return await _mem_cache.get(key)
        except Exception:
            return None


async def cache_set(key: str, value: str, ttl: int = 600) -> None:
    """写入缓存（默认 TTL 10 分钟）；Redis 或内存兜底。"""
    try:
        await _backend().set(key, value, ttl)
    except Exception:
        try:
            await _mem_cache.set(key, value, ttl)
        except Exception:
            return


async def cache_delete(key: str) -> None:
    """删除缓存键。"""
    try:
        await _backend().delete(key)
    except Exception:
        try:
            await _mem_cache.delete(key)
        except Exception:
            return


async def rate_incr(key: str, ttl: int = 86400) -> int:
    """限流计数：INCR 并在首次设过期（默认 1 天）；返回当前计数。

    无 Redis / 异常时降级为内存计数（仍返回有效计数，不返回 None）。
    """
    try:
        backend = _backend()
        if isinstance(backend, _MemCache):
            return await backend.incr(key, ttl)
        # Redis 风格客户端：incr 不直接收 ttl，首次设过期
        n = await backend.incr(key)
        if n == 1:
            try:
                await backend.expire(key, ttl)
            except Exception:
                pass
        return n
    except Exception:
        try:
            return await _mem_cache.incr(key, ttl)
        except Exception:
            return 0
