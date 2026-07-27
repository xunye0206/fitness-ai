"""M9 agent 工具联动测试：对话接口能真正调用 diet/training/report 业务动作。

全部走 fake 模型 + SQLite，不联网、不填 key。
重点验证：① 图片 → 自动建饮食记录；② 各工具 handler 落地；③ 工具调用循环把意图转成动作。
端点为 SSE 流式，测试解析 SSE 帧。
"""
import asyncio
import json

from sqlmodel import select

from app.agent import api as agent_api
from app.agent.tools import execute_tool
from app.core.db import async_session_factory
from app.llm.base import ToolCall
from app.modules.diet.domain import DietEntry
from app.modules.report.domain import DailyReport
from app.modules.training.domain import TrainingEntry


def _parse_sse(text: str) -> dict:
    reply = ""
    actions = []
    ok = True
    for frame in text.split("\n\n"):
        lines = [l for l in frame.split("\n") if l.startswith("data:")]
        if not lines:
            continue
        ev = json.loads(lines[0][5:].strip())
        if ev["type"] == "delta":
            reply += ev["text"]
        elif ev["type"] == "action":
            actions.append(ev["text"])
        elif ev["type"] == "done":
            ok = ev["ok"]
    return {"reply": reply, "actions": actions, "ok": ok}


def _auth(client):
    client.post("/auth/register", json={"username": "tooltester", "password": "p1234567"})
    tok = client.post("/auth/login", json={"username": "tooltester", "password": "p1234567"}).json()["access_token"]
    uid = client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()["id"]
    return tok, uid


def test_chat_image_creates_diet_entry(client):
    tok, uid = _auth(client)
    # 1x1 透明 PNG 的 base64（不含 data: 前缀）
    img = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
    r = client.post("/agent/chat", json={"message": "", "image_base64": img},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    d = _parse_sse(r.text)
    assert d["ok"] is True
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
    """模型首轮发起工具调用 → 执行 → 次轮流式生成自然语言回复（多轮循环）。

    注意：新 AgentLoop 会反复调用 reason_stream_with_tools 直到模型不再要工具，
    因此这里的假函数必须「有状态」——第 1 次返回工具调用，第 2 次返回最终文本，
    否则循环会一直触发工具调用、写多条重复记录。
    """
    state = {"n": 0}

    async def fake_reason_stream_with_tools(messages, tools, tool_choice="auto"):
        state["n"] += 1
        if state["n"] == 1:
            yield {"type": "tools", "calls": [
                ToolCall(id="c1", name="log_training",
                         arguments={"exercise_type": "游泳", "duration_min": 40, "intensity": "high"})
            ]}
        else:
            yield {"type": "delta", "text": "好的，已帮你记录游泳 40 分钟高强度训练！"}

    async def fake_reason_stream(messages, tools=None):
        yield "好的，已帮你记录游泳 40 分钟高强度训练！"

    monkeypatch.setattr(agent_api, "reason_stream_with_tools", fake_reason_stream_with_tools)
    monkeypatch.setattr(agent_api, "reason_stream", fake_reason_stream)

    tok, uid = _auth(client)
    r = client.post("/agent/chat", json={"message": "帮我记录游泳40分钟高强度"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    d = _parse_sse(r.text)
    assert d["ok"] is True
    assert "游泳" in d["reply"]
    assert any("已记录训练" in a for a in d["actions"])
    async def run():
        async with async_session_factory() as s:
            return (await s.execute(select(TrainingEntry).where(TrainingEntry.user_id == uid))).scalars().all()
    rows = asyncio.run(run())
    assert len(rows) == 1 and rows[0].exercise_type == "游泳"
