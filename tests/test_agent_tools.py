"""M9 agent 工具联动测试：对话接口能真正调用 diet/training/report 业务动作。

全部走 fake 模型 + SQLite，不联网、不填 key。
重点验证：① 图片 → 自动建饮食记录；② 各工具 handler 落地；③ 工具调用循环把意图转成动作。
"""
import asyncio

from sqlmodel import select

from app.agent import api as agent_api
from app.agent.tools import execute_tool
from app.core.db import async_session_factory
from app.llm.base import LLMResult, ToolCall
from app.modules.diet.domain import DietEntry
from app.modules.report.domain import DailyReport
from app.modules.training.domain import TrainingEntry


def _auth(client):
    client.post("/auth/register", json={"username": "tooltester", "password": "p123456"})
    tok = client.post("/auth/login", json={"username": "tooltester", "password": "p123456"}).json()["access_token"]
    uid = client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()["id"]
    return tok, uid


def test_chat_image_creates_diet_entry(client):
    tok, uid = _auth(client)
    # 1x1 透明 PNG 的 base64（不含 data: 前缀）
    img = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
    r = client.post("/agent/chat", json={"message": "", "image_base64": img},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    async def run():
        async with async_session_factory() as s:
            return (await s.execute(select(DietEntry).where(DietEntry.user_id == uid))).scalars().all()
    rows = asyncio.run(run())
    assert len(rows) >= 1            # 图片已自动记录为饮食
    assert rows[0].name != ""        # fake 视觉给了食物名


def test_execute_tool_log_training(client):
    tok, uid = _auth(client)
    async def run():
        async with async_session_factory() as s:
            out = await execute_tool(
                "log_training",
                {"exercise_type": "跑步", "duration_min": 30, "intensity": "medium", "calories_burned": 280},
                s, uid,
            )
            rows = (await s.execute(select(TrainingEntry).where(TrainingEntry.user_id == uid))).scalars().all()
            return out, rows
    out, rows = asyncio.run(run())
    assert "已记录训练" in out
    assert len(rows) == 1 and rows[0].exercise_type == "跑步"


def test_execute_tool_log_diet_from_text(client):
    tok, uid = _auth(client)
    async def run():
        async with async_session_factory() as s:
            out = await execute_tool("log_diet_from_text", {"description": "中午吃了牛肉面"}, s, uid)
            rows = (await s.execute(select(DietEntry).where(DietEntry.user_id == uid))).scalars().all()
            return out, rows
    out, rows = asyncio.run(run())
    assert "已记录饮食" in out
    assert len(rows) == 1


def test_execute_tool_generate_report(client):
    tok, uid = _auth(client)
    async def run():
        async with async_session_factory() as s:
            out = await execute_tool("generate_report", {"days": 7}, s, uid)
            rows = (await s.execute(select(DailyReport).where(DailyReport.user_id == uid))).scalars().all()
            return out, rows
    out, rows = asyncio.run(run())
    assert "已生成" in out
    assert len(rows) == 1


def test_chat_tool_loop_invokes_execute(client, monkeypatch):
    """模型首轮发起工具调用 → 执行 → 次轮生成自然语言回复。"""
    call1 = LLMResult(
        text="", ok=True,
        tool_calls=[ToolCall(id="c1", name="log_training",
                             arguments={"exercise_type": "游泳", "duration_min": 40, "intensity": "high"})],
    )
    call2 = LLMResult(text="好的，已帮你记录游泳 40 分钟高强度训练！", ok=True)
    q = [call1, call2]

    async def fake_reason_with_tools(messages, tools, tool_choice="auto"):
        return q.pop(0)

    async def fake_reason(messages):
        return q.pop(0)

    monkeypatch.setattr(agent_api, "reason_with_tools", fake_reason_with_tools)
    monkeypatch.setattr(agent_api, "reason", fake_reason)

    tok, uid = _auth(client)
    r = client.post("/agent/chat", json={"message": "帮我记录游泳40分钟高强度"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert "游泳" in d["reply"]
    assert any("已记录训练" in a for a in d["actions"])
    async def run():
        async with async_session_factory() as s:
            return (await s.execute(select(TrainingEntry).where(TrainingEntry.user_id == uid))).scalars().all()
    rows = asyncio.run(run())
    assert len(rows) == 1 and rows[0].exercise_type == "游泳"
