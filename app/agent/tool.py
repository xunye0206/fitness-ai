"""工具抽象（对应 OpenCode 的 BaseTool 接口）。

每个工具是一个「能力单元」：有名字、描述、参数 JSON schema，以及一个 run 方法。
Agent 通过 ToolRegistry 按名字找到工具并执行；工具本身不感知循环 / 提示词 /
LLM，从而可插拔、可单独测试。这正是它比旧版「裸 dict + if/elif 分发」更干净之处。
"""
from abc import ABC, abstractmethod
from typing import Any, Optional

from app.agent.types import ToolContext, ToolResult


class Tool(ABC):
    """一个可被 Agent 调用的能力。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名（模型据此选择是否调用，必须全局唯一）。"""

    @property
    @abstractmethod
    def description(self) -> str:
        """给模型看的能力说明（决定模型何时调用它）。"""

    @abstractmethod
    def parameters_schema(self) -> dict:
        """参数 JSON schema（OpenAI function-calling 格式：type / properties / required）。"""

    @abstractmethod
    async def run(self, arguments: dict, ctx: ToolContext) -> ToolResult:
        """执行工具，返回结构化结果（回填给模型）。

        arguments 已是解析后的 dict；ctx 提供 user_id / session。
        任何内部异常都应在 run 内捕获并转为 ToolResult(is_error=True)，
        或交由 ToolRegistry.execute 统一兜底。
        """

    # 以下两个为「可执行能力的元数据」，默认 False，子类按需覆盖。
    # 不设为 abstractmethod——旧/未声明的工具无需改即可继续工作。
    def is_destructive(self) -> bool:
        """是否为破坏性操作（如删除/覆盖）。默认 False。

        护栏据此要求调用方显式带 confirmed=true 才放行（见 CoachToolGuardrail）。
        """
        return False

    def is_write(self) -> bool:
        """是否写库（产生持久化副作用）。默认 False。

        护栏据此做「单轮回写上限」防失控——模型若在工具循环里
        反复触发写工具，会在达到上限后被拦截，避免 DB 被刷爆 + 烧钱。
        """
        return False


class ToolGuardrail(ABC):
    """工具执行前的护栏（对应 OpenCode 的 permission.Request）。

    返回非空的拒绝理由字符串表示拦截；返回 None（或空串）表示放行。
    典型用途：合规模式禁用某些动作、对不可逆动作要求确认、按会话限流等。
    """

    @abstractmethod
    async def check(self, name: str, arguments: dict, ctx: ToolContext) -> Optional[str]:
        ...
