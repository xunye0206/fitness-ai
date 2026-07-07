"""Redis 客户端单例 + 优雅降级。

用途：
- agent 上下文热缓存（build_context 命中即返，避免每次重算 7 天聚合）。
- 推送 ≤3/天 限流计数。

降级策略：REDIS_URL 为空 / redis 包未装 / 连不上 → 所有操作返回 None/空，调用方按
"无缓存"处理，绝不抛异常、不拖垮主链路（饮食/训练/报告/推送）。
"""
import logging
from functools import lru_cache
from typing import Optional

from app.config import settings

logger = logging.getLogger("fitness_agent.redis")

try:
    import redis.asyncio as aioredis

    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False


@lru_cache
def get_redis():
    """返回 redis.asyncio 客户端；不可用时返回 None（调用方降级）。"""
    if not settings.redis_url or not _HAS_REDIS:
        return None
    try:
        return aioredis.from_url(
            settings.redis_url, decode_responses=True, socket_connect_timeout=2
        )
    except Exception as exc:
        logger.warning("Redis 客户端创建失败，已降级为无缓存：%s", exc)
        return None


async def cache_get(key: str) -> Optional[str]:
    """读取缓存；无 Redis/异常返回 None。"""
    r = get_redis()
    if r is None:
        return None
    try:
        return await r.get(key)
    except Exception:
        return None


async def cache_set(key: str, value: str, ttl: int = 600) -> None:
    """写入缓存（默认 TTL 10 分钟）；无 Redis/异常静默跳过。"""
    r = get_redis()
    if r is None:
        return
    try:
        await r.set(key, value, ex=ttl)
    except Exception:
        return


async def cache_delete(key: str) -> None:
    """删除缓存键；无 Redis/异常静默跳过。"""
    r = get_redis()
    if r is None:
        return
    try:
        await r.delete(key)
    except Exception:
        return


async def rate_incr(key: str, ttl: int = 86400) -> Optional[int]:
    """限流计数：INCR 并在首次设过期（默认 1 天）；返回当前计数；无 Redis 返回 None。"""
    r = get_redis()
    if r is None:
        return None
    try:
        n = await r.incr(key)
        if n == 1:
            await r.expire(key, ttl)
        return n
    except Exception:
        return None
