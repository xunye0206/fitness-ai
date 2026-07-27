"""工具注册表（参考 OpenCode「构造注入 []BaseTool」模式实现）。

单一数据源：工具在此注册一次，既供 AgentLoop 调度执行，也供 API 层生成 TOOLS
schema，避免工具定义与调度两处维护脱节。
"""
from typing import Optional

from app.agent.tool import Tool, ToolGuardrail
from app.agent.types import ToolContext, ToolResult
from app.agent.validation import validate_arguments


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册一个工具（同名覆盖）。"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict]:
        """导出 OpenAI function-calling 格式的工具清单，直接喂给 LLM。"""
        out: list[dict] = []
        for t in self._tools.values():
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters_schema(),
                    },
                }
            )
        return out

    async def execute(
        self,
        name: str,
        arguments: dict,
        ctx: ToolContext,
        guardrail: Optional[ToolGuardrail] = None,
    ) -> ToolResult:
        """按名执行工具，返回 ToolResult。

        失败兜底（不抛异常、不中断主链路）：
        - 工具不存在 → is_error=True 的「未知工具」结果
        - 护栏拒绝   → is_error=True 的「被拦截」结果，不执行工具
        - 执行抛异常 → is_error=True 的「执行失败」结果
        """
        tool = self.get(name)
        if tool is None:
            return ToolResult(content=f"未知工具：{name}", is_error=True)
        if guardrail is not None:
            denial = await guardrail.check(name, arguments or {}, ctx)
            if denial:
                return ToolResult(
                    content=f"工具 {name} 被护栏拦截：{denial}", is_error=True
                )
        # 执行前参数校验（类型强转 + 必填报检），脏参数优雅报错而非在 run 里崩
        ok, coerced, err = validate_arguments(tool.parameters_schema(), arguments or {})
        if not ok:
            return ToolResult(content=f"工具 {name} 参数错误：{err}", is_error=True)
        try:
            return await tool.run(coerced, ctx)
        except Exception as exc:  # 兜底：任何异常都降级为可读结果
            return ToolResult(content=f"工具 {name} 执行失败：{exc}", is_error=True)
