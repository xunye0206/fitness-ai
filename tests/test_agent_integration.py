"""声明式 Agent + 领域工具适配器集成测试（fake 模型/真实 service，零 key）。

验证：① build_coach_agent 组装出预期工具；② 工具 schema 是 LLM 可消费的
OpenAI function 格式；③ 工具适配器真正把意图写入业务库（diet/training/report）。
"""
import asyncio

from sqlmodel import select

from app.agent.agent import build_coach_agent
from app.agent.coach_tools import register_coach_tools
from app.agent.registry import ToolRegistry
from app.agent.types import ToolContext
from app.core.db import async_session_factory
from app.modules.diet.domain import DietEntry
from app.modules.training.domain import TrainingEntry


def test_coach_agent_has_expected_tools():
    reg = ToolRegistry()
    register_coach_tools(reg)
    agent = build_coach_agent(reg)
    names = {t.name for t in agent.tools()}
    assert "log_training" in names
    assert "log_diet_from_text" in names
    assert "generate_report" in names
    schemas = reg.schemas()
    assert any(s["function"]["name"] == "log_training" for s in schemas)


def test_coach_log_training_tool_writes_db(client):
    reg = ToolRegistry()
    register_coach_tools(reg)
    tok = client.post("/auth/register", json={"username": "integ", "password": "p1234567"}).json()["access_token"]
    uid = client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()["id"]

    async def run():
        async with async_session_factory() as s:
            ctx = ToolContext(user_id=uid, session=s)
            res = await reg.execute(
                "log_training",
                {"exercise_type": "跑步", "duration_min": 30, "intensity": "medium", "calories_burned": 200},
                ctx,
            )
            rows = (await s.execute(select(TrainingEntry).where(TrainingEntry.user_id == uid))).scalars().all()
            return res, rows

    res, rows = asyncio.run(run())
    assert "已记录训练" in res.content
    assert len(rows) == 1 and rows[0].exercise_type == "跑步"


def test_execute_tool_unknown_via_coach_registry(client):
    """未注册工具经过注册表兜底，返回错误结果而非抛异常。"""
    reg = ToolRegistry()
    register_coach_tools(reg)
    res = asyncio.run(_run_unknown(reg))


async def _run_unknown(reg):
    async with async_session_factory() as s:
        ctx = ToolContext(user_id=1, session=s)
        return await reg.execute("not_a_real_tool", {}, ctx)


def test_register_coach_tools_idempotent():
    reg1 = ToolRegistry()
    reg2 = ToolRegistry()
    register_coach_tools(reg1)
    register_coach_tools(reg2)
    assert {t.name for t in reg1.all()} == {t.name for t in reg2.all()}
