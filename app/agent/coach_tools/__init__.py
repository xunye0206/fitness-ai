"""领域工具适配器：把 diet / training / report 业务动作封装成 Agent 可调用工具。

每个工具是一个独立、可插拔的单元（继承 Tool），在 register_coach_tools 里统一
注册进 ToolRegistry。要新增一个能力（如 schedule_push / query_weight），只需写
一个 Tool 子类并在此注册，AgentLoop 与 LLM schema 会自动感知——无需动循环或 API。
"""
from app.agent.registry import ToolRegistry
from app.agent.coach_tools.diet import LogDietFromTextTool
from app.agent.coach_tools.training import LogTrainingTool
from app.agent.coach_tools.report import GenerateReportTool


def register_coach_tools(registry: ToolRegistry) -> ToolRegistry:
    registry.register(LogDietFromTextTool())
    registry.register(LogTrainingTool())
    registry.register(GenerateReportTool())
    return registry


__all__ = [
    "register_coach_tools",
    "LogDietFromTextTool",
    "LogTrainingTool",
    "GenerateReportTool",
]
