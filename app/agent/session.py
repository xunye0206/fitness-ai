"""服务端会话管理（对应 OpenCode 的 Session + 压缩指针模式）。

旧版把对话历史完全交给前端（payload.history）传来传去，服务器无状态、长对话会
越攒越长、且换设备/刷新就丢。这里引入可服务端托管的会话：

- SessionStore：历史持久化位置（内存实现 / SQLite 实现）。
- SessionManager：追加/读取历史；当 token 估算超预算时，用 summarizer 把整段
  历史压成一条 system 摘要消息（「压缩指针」），避免无脑截断导致教练「变笨」。

前端仍可选带 history 走兼容路径；带 session_id 时以服务端为准。
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel, delete, select

from app.core.db import async_session_factory

logger = logging.getLogger("fitness_agent.session")


@dataclass
class StoredMessage:
    role: str
    content: str
    tool_calls: list = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    def approx_tokens(self) -> int:
        """粗估 token 数（不精确，只用于判断是否触发压缩）。

        中英文混排：按字符数 / 2 估一个保守上限，避免频繁误触发压缩。
        """
        base = len(self.content) // 2
        extra = sum(len(str(t)) for t in self.tool_calls) // 4
        return max(1, base + extra)


class SessionStore:
    """历史持久化抽象。"""

    async def load(self, session_id: str) -> list[StoredMessage]:
        raise NotImplementedError

    async def save(self, session_id: str, messages: list[StoredMessage]) -> None:
        raise NotImplementedError


class InMemorySessionStore(SessionStore):
    """进程内内存实现（单进程部署够用；多 worker / 重启需换外部存储）。"""

    def __init__(self) -> None:
        self._data: dict[str, list[StoredMessage]] = {}

    async def load(self, session_id: str) -> list[StoredMessage]:
        return list(self._data.get(session_id, []))

    async def save(self, session_id: str, messages: list[StoredMessage]) -> None:
        self._data[session_id] = list(messages)


class SessionManager:
    def __init__(
        self,
        store: SessionStore,
        max_tokens: int = 4000,
        summarizer: Optional[Callable[[list[StoredMessage]], Awaitable[str]]] = None,
    ) -> None:
        self.store = store
        self.max_tokens = max_tokens
        self.summarizer = summarizer

    async def get_history(self, session_id: str) -> list[StoredMessage]:
        return await self.store.load(session_id)

    async def append(self, session_id: str, msg: StoredMessage) -> None:
        hist = await self.store.load(session_id)
        hist.append(msg)
        await self.store.save(session_id, hist)

    async def compact_if_needed(self, session_id: str) -> bool:
        """若历史 token 估算超预算且提供了 summarizer，则压缩为一条摘要。

        返回 True 表示发生了压缩；否则 False（未超预算或未配置 summarizer）。
        """
        hist = await self.store.load(session_id)
        total = sum(m.approx_tokens() for m in hist)
        if total <= self.max_tokens or self.summarizer is None:
            return False
        summary = await self.summarizer(hist)
        await self.store.save(session_id, [StoredMessage(role="system", content=summary)])
        logger.info("会话 %s 已压缩：%d tokens → 1 条摘要", session_id, total)
        return True


class SessionMessage(SQLModel, table=True):
    """会话消息持久化表（零成本 SQLite 文件存储，重启不丢）。

    挂在 SQLModel.metadata 上，conftest 的 create_all 会自动建表。
    """

    __tablename__ = "agent_session_messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    role: str
    content: str = ""
    tool_calls_json: str = "[]"
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True)),
    )


class SqliteSessionStore(SessionStore):
    """基于现有 SQLite 引擎的持久化存储（零额外依赖，重启不丢会话）。

    复用 app.core.db 的 async_session_factory，与会话消息表同库。
    单进程/小并发足够；高并发或分布式部署可换成 Redis 实现（接口一致）。
    """

    async def load(self, session_id: str) -> List[StoredMessage]:
        async with async_session_factory() as s:
            rows = (
                await s.execute(
                    select(SessionMessage)
                    .where(SessionMessage.session_id == session_id)
                    .order_by(SessionMessage.id)
                )
            ).scalars().all()
            return [
                StoredMessage(
                    role=r.role,
                    content=r.content,
                    tool_calls=json.loads(r.tool_calls_json or "[]"),
                    tool_call_id=r.tool_call_id,
                    name=r.name,
                )
                for r in rows
            ]

    async def save(self, session_id: str, messages: List[StoredMessage]) -> None:
        async with async_session_factory() as s:
            await s.execute(
                delete(SessionMessage).where(SessionMessage.session_id == session_id)
            )
            for m in messages:
                s.add(
                    SessionMessage(
                        session_id=session_id,
                        role=m.role,
                        content=m.content,
                        tool_calls_json=json.dumps(m.tool_calls or []),
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                    )
                )
            await s.commit()
