"""LLM 供应商抽象层（业务唯一依赖的 LLM 契约）。

业务层只调用 app.llm.router 的 reason/see/embed，绝不直接 import 任何供应商 SDK。
新增非 OpenAI 兼容供应商时，只需新建子类继承 LLMProvider 并注册进 registry。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Capability(str, Enum):
    TEXT = "text"
    VISION = "vision"
    EMBEDDING = "embedding"


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResult:
    """统一返回结构。失败时不抛异常，用 ok=False + error 表达，便于降级。"""

    text: str
    ok: bool = True
    error: Optional[str] = None
    raw: Optional[dict[str, Any]] = None


class LLMProvider:
    name: str = "base"
    capabilities: set[Capability] = field(default_factory=set)

    async def reason(self, messages: list[Message]) -> LLMResult:
        raise NotImplementedError

    async def see(self, image_base64: str, prompt: str) -> LLMResult:
        raise NotImplementedError

    async def embed(self, texts: list[str]) -> LLMResult:
        raise NotImplementedError
