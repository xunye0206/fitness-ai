"""Agent 层共享数据类型（纯结构，无业务依赖）。

这些类型把「工具结果 / 循环事件 / 工具上下文」从具体实现里抽出来，
让 AgentLoop、Registry、API 都围着同一套小数据结构转，便于独立测试。
"""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolResult:
    """工具执行结果，回填给模型。

    is_error=True 表示执行失败或被护栏拦截（例如未知工具、抛出异常）。
    AgentLoop 会把它作为 tool 消息回灌，绝不因此中断主链路。
    """

    content: str
    is_error: bool = False


@dataclass
class AgentEvent:
    """Agent 循环产出的事件，API 层再翻译成 SSE 帧。

    type 取值：
    - "delta"   ：一段自然语言文本，应逐字推给前端
    - "action"  ：一个工具已被执行的人类可读注记（如「已记录训练：跑步 30 分钟」）
    """

    type: str
    text: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class ToolContext:
    """工具执行上下文：当前用户身份 + 数据库会话（可选）。

    工具方法的签名统一为 run(arguments, ctx)，从 ctx 取 user_id / session，
    而不直接依赖全局状态——这样才能在测试里注入假 session 单独验证。
    request_id 用于跨日志串联一次对话的多轮工具调用（可观测性）。
    """

    user_id: int
    session: Any = None
    request_id: Optional[str] = None
