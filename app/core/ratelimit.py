"""轻量内存限流依赖，防登录/注册爆破。

说明：
- 单进程内生效（uvicorn 单 worker 时足够）。
- 多 worker / 生产高并发建议换 nginx 限流或 Redis 计数器。
- 按 (路由, 客户端IP) 维度在滑动时间窗内计数。
- _ENABLED 为 False 时整体旁路（测试会话内关闭，避免共用 127.0.0.1 触发 429）。
"""
from collections import defaultdict, deque

from fastapi import Request, HTTPException, status
import time

# key: (route, ip) -> deque[float(timestamp)]
_hits: dict[tuple, deque] = defaultdict(deque)
_ENABLED = True  # 测试环境可置 False 关闭


def set_enabled(value: bool) -> None:
    """测试夹具调用：关闭后限流整体旁路（不计数、不拦截）。"""
    global _ENABLED
    _ENABLED = value


def reset() -> None:
    """清空所有计数（测试用，保证用例从干净状态开始）。"""
    _hits.clear()


def rate_limit(max_requests: int, window_seconds: int, route: str):
    """返回一个 FastAPI 依赖，挂到端点装饰器的 dependencies=[...] 上。"""

    async def _depend(request: Request) -> None:
        if not _ENABLED:
            return
        ip = request.client.host if request.client else "unknown"
        key = (route, ip)
        now = time.monotonic()
        dq = _hits[key]
        # 丢弃窗口外的旧请求时间戳
        while dq and now - dq[0] > window_seconds:
            dq.popleft()
        if len(dq) >= max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"请求过于频繁，请 {window_seconds} 秒后再试",
            )
        dq.append(now)

    return _depend
