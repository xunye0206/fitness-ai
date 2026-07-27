"""教练 Agent 的默认工具护栏（执行层硬边界）。

弥补旧版 `AgentLoop(guardrail=None)` 的缺口——之前 `ToolGuardrail` 这个
设计好的边界**根本没接实例**，任何工具被模型选中就直接执行。

对应 OpenCode 的 permission 中间件思路：
- 黑名单：永不许模型触发的工具（如未来出现的 delete_*）。
- 破坏性需确认：is_destructive 工具必须带 confirmed=true 才放行。
- 单轮回写上限：模型若在工具循环里反复触发写库，达到上限后拦截，
  防 DB 被刷爆 + 防失控烧钱（直接服务「成本控制」维度）。

本护栏是有状态的（每请求建一个实例，_writes 计数在本轮内有效）。
"""
from typing import Optional

from app.agent.registry import ToolRegistry
from app.agent.tool import ToolGuardrail
from app.agent.types import ToolContext

# 永不允许被模型触发的工具名（代码层硬拒，对应 OpenCode bannedCommands）
DENY_LIST: set[str] = set()


class CoachToolGuardrail(ToolGuardrail):
    def __init__(self, registry: ToolRegistry, max_writes_per_request: int = 10) -> None:
        self._registry = registry
        self._max_writes = max_writes_per_request
        self._writes = 0

    async def check(self, name: str, arguments: dict, ctx: ToolContext) -> Optional[str]:
        # 1) 黑名单硬拒
        if name in DENY_LIST:
            return f"工具 {name} 在黑名单中，禁止调用"

        tool = self._registry.get(name)
        if tool is None:
            # 未知工具由 registry.execute 兜底，这里不拦（交给它返回「未知工具」）
            return None

        # 2) 破坏性操作需显式确认
        if tool.is_destructive() and not (arguments or {}).get("confirmed"):
            return f"工具 {name} 为破坏性操作，需带 confirmed=true 确认后执行"

        # 3) 单轮回写上限（防失控刷库 / 烧钱）
        if tool.is_write():
            if self._writes >= self._max_writes:
                return f"本轮回写已达上限（{self._max_writes}），为避免失控已暂停写库"
            self._writes += 1

        return None
