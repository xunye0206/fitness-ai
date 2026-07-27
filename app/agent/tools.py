"""兼容层：对外暴露 TOOLS（OpenAI function schema）与 execute_tool（按名分发）。

新架构下工具实现在 app.agent.coach_tools，经 ToolRegistry 注册。此模块只做聚合与
向后兼容包装——保持旧接口签名（TOOLS 列表、execute_tool(name, args, session, user_id)），
使 api.py 与既有测试无需改动。

TOOLS 不再是手维护的列表，而是注册表单一数据源的导出，杜绝「schema 与实现脱节」。
"""
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent.coach_tools import register_coach_tools
from app.agent.registry import ToolRegistry
from app.agent.types import ToolContext

# 进程级单一注册表（与 build_coach_agent 共用同一份，避免重复注册）
REGISTRY = ToolRegistry()
register_coach_tools(REGISTRY)

# 供 api.py 传给 LLM 的工具 schema（= 注册表单一数据源导出）
TOOLS = REGISTRY.schemas()


async def execute_tool(name: str, arguments: dict, session: AsyncSession, user_id: int) -> str:
    """兼容旧接口：按名分发到注册表工具，返回人类可读结果字符串。

    内部走 ToolRegistry.execute（含未知工具 / 异常兜底），不再有 if/elif 分发。
    """
    result = await REGISTRY.execute(
        name, arguments or {}, ToolContext(user_id=user_id, session=session)
    )
    return result.content
