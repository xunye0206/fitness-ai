"""pytest 全局夹具。

- 测试前把数据库指向独立文件，并替换默认 jwt_secret（避免误用生产库）。
- 用法：业务测试直接 `def test_x(client): ...`，client 已带测试 DB 与 FakeProvider。
- 全程不联网、不依赖真实 API key。
"""
import asyncio
import os
import shutil
import pytest

# 必须在导入 app 之前设置测试环境
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_fitness.db"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["UPLOAD_DIR"] = "./test_uploads"  # 测试上传目录，避免污染 data/
# 测试强制走 fake 模型，隔离用户本地 .env 里的真模型配置（env 优先级高于 .env）
os.environ["REASONING_PROVIDER"] = "fake"
os.environ["VISION_PROVIDER"] = "fake"
os.environ["EMBEDDING_PROVIDER"] = ""

from app.config import get_settings

get_settings.cache_clear()  # 让 settings 用上面覆盖的测试值

from app.main import app  # noqa: E402  (必须在 env/cache 设置后导入)
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402


def _create_all() -> None:
    async def _run() -> None:
        from app.core.db import engine

        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    asyncio.run(_run())


def _drop_all() -> None:
    async def _run() -> None:
        from app.core.db import engine

        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)

    asyncio.run(_run())


@pytest.fixture
def client():
    _create_all()
    with TestClient(app) as c:
        yield c
    _drop_all()
    shutil.rmtree("test_uploads", ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_mem_cache():
    """每个测试后清空进程内内存缓存，避免跨测试污染限流/上下文 key。

    生产环境单进程内内存缓存应跨请求共享（这是缓存的意义）；仅在测试中
    隔离，保证每个用例从干净状态开始。
    """
    yield
    from app.core import redis as _redis

    _redis._mem_cache._store.clear()
