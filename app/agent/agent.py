"""声明式 Agent 定义（对应 OpenCode 的 config.Agent + GetAgentPrompt）。

旧版把「谁是教练、用哪些工具、系统提示词写死在哪」散落在 API 代码里。这里把它
抽成一个数据驱动的定义：一个 AgentConfig 描述名字 / 提示词 / 可用工具名 / 循环
上限，FitnessCoachAgent 只是这个配置 + 工具注册表的薄封装。要加一个新 agent
（比如未来的「计划 agent」「研究 agent」）只需再写一个 config，无需改循环。
"""
from dataclasses import dataclass

from app.agent.prompts import COACH_SYSTEM_PROMPT
from app.agent.registry import ToolRegistry
from app.agent.tool import Tool


@dataclass
class AgentConfig:
    name: str
    system_prompt: str
    tool_names: list[str]
    max_iterations: int = 6


class FitnessCoachAgent:
    def __init__(self, config: AgentConfig, registry: ToolRegistry) -> None:
        self.config = config
        self.registry = registry

    @property
    def system_prompt(self) -> str:
        return self.config.system_prompt

    @property
    def max_iterations(self) -> int:
        return self.config.max_iterations

    def tools(self) -> list[Tool]:
        """返回该 agent 实际可用的工具对象（跳过未注册的名字，容错）。"""
        out: list[Tool] = []
        for n in self.config.tool_names:
            t = self.registry.get(n)
            if t is not None:
                out.append(t)
        return out


# 教练 agent 的默认工具清单（与 tools.py 注册的工具名一一对应）
COACH_TOOL_NAMES = ["log_diet_from_text", "log_training", "generate_report"]


def build_coach_agent(registry: ToolRegistry) -> FitnessCoachAgent:
    """组装健身教练 agent（单一入口）。"""
    return FitnessCoachAgent(
        AgentConfig(
            name="fitness_coach",
            system_prompt=COACH_SYSTEM_PROMPT,
            tool_names=COACH_TOOL_NAMES,
            max_iterations=6,
        ),
        registry,
    )
