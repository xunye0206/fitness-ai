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
