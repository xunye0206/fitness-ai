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
class ToolCall:
    """模型发起的一次工具调用（函数调用）。arguments 为已解析的 dict。"""
    id: str
    name: str
    arguments: dict


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_calls: list = field(default_factory=list)  # 仅 assistant 消息：list[ToolCall]
    tool_call_id: Optional[str] = None  # 仅 tool 结果消息：对应 ToolCall.id
    name: Optional[str] = None  # 仅 tool 结果消息：被调用的函数名


@dataclass
class LLMResult:
    """统一返回结构。失败时不抛异常，用 ok=False + error 表达，便于降级。"""

    text: str
    ok: bool = True
    error: Optional[str] = None
    raw: Optional[dict[str, Any]] = None
    tool_calls: list = field(default_factory=list)  # list[ToolCall]，函数调用模式下非空


class LLMProvider:
    name: str = "base"
    capabilities: set[Capability] = field(default_factory=set)

    async def reason(self, messages: list[Message]) -> LLMResult:
        raise NotImplementedError

    async def reason_stream(self, messages: list[Message], tools=None, tool_choice="auto"):
        """流式推理（逐字 yield 文本）。默认未实现。"""
        raise NotImplementedError

    async def reason_stream_with_tools(self, messages: list[Message], tools: list[dict], tool_choice: str = "auto"):
        """流式推理并检测工具调用：yield {"type":"delta","text":...} 或 {"type":"tools","calls":[ToolCall]}。默认未实现。"""
        raise NotImplementedError

    async def see(self, image_base64: str, prompt: str) -> LLMResult:
        raise NotImplementedError

    async def embed(self, texts: list[str]) -> LLMResult:
        raise NotImplementedError
