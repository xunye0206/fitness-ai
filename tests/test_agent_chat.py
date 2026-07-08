"""M8：AI 教练对话接口测试（fake 模型，零网络零 key）。

端点改为 SSE 流式输出，测试解析 SSE 帧聚合成 {reply, actions, ok}。
"""
import json

import pytest

from app.agent import api as agent_api
from app.agent.guardrails import DISCLAIMER_TEXT
from app.llm.base import LLMResult
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _noop_profile_bg_update(monkeypatch):
    # M10：/agent/chat 在流式返回后会 fire-and-forget 触发画像更新（asyncio.create_task）。
    # 同步 TestClient 在事件循环关闭时会等待该后台任务，导致测试挂起；故测试里替换为
    # no-op。画像「抽取+写入」能力由 tests/test_profile.py 以同步方式（传 session）单独验证。
    monkeypatch.setattr(agent_api, "schedule_profile_update", lambda *a, **k: None)


def _token(client: TestClient) -> str:
    r = client.post("/auth/register", json={"username": "coachtester", "password": "secret123"})
    return r.json()["access_token"]


def _parse_sse(text: str) -> dict:
    """把 SSE 文本聚合成 {reply, actions, ok}。"""
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


def test_chat_requires_auth(client: TestClient):
    r = client.post("/agent/chat", json={"message": "你好教练"})
    assert r.status_code == 401


def test_chat_returns_reply(client: TestClient):
    token = _token(client)
    r = client.post(
        "/agent/chat",
        json={"message": "今晚想练胸，给个安排"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    d = _parse_sse(r.text)
    assert d["ok"] is True
    # fake 模型返回固定前缀；说明链路（鉴权→上下文→推理）跑通
    assert "fake-reason" in d["reply"]


def test_chat_accepts_history(client: TestClient):
    token = _token(client)
    r = client.post(
        "/agent/chat",
        json={
            "message": "那饮食呢",
            "history": [
                {"role": "user", "content": "今晚想练胸"},
                {"role": "assistant", "content": "好的，做卧推"},
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    d = _parse_sse(r.text)
    assert d["ok"] is True


def test_chat_appends_disclaimer_for_normal_reply(client: TestClient):
    """里程碑2：正常回复后自动追加免责声明（对应代码规范 §12）。"""
    token = _token(client)
    r = client.post(
        "/agent/chat",
        json={"message": "今晚想练背"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    d = _parse_sse(r.text)
    assert d["ok"] is True
    assert DISCLAIMER_TEXT in d["reply"]


def test_chat_flags_forbidden_term_and_appends_disclaimer(client: TestClient, monkeypatch):
    """里程碑2：回复含越界医疗措辞时，追加合规提示 + 免责声明，原文保留。"""
    async def fake_reason_stream_with_tools(messages, tools, tool_choice="auto"):
        yield {"type": "delta", "text": "凭数据看，你患有高血脂，建议服用他汀。"}

    monkeypatch.setattr(agent_api, "reason_stream_with_tools", fake_reason_stream_with_tools)

    token = _token(client)
    r = client.post(
        "/agent/chat",
        json={"message": "帮我看看指标"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    d = _parse_sse(r.text)
    assert d["ok"] is True
    assert "你患有高血脂" in d["reply"]     # 原回复保留
    assert "不提供医疗诊断" in d["reply"]   # 合规提示已追加
    assert DISCLAIMER_TEXT in d["reply"]    # 免责声明已追加


def test_chat_llm_review_flags_rewritten_bypass(client: TestClient, monkeypatch):
    """里程碑2 增强：关键词漏检的改写绕过，被 LLM 语义复核拦下并补提示+声明。"""
    async def fake_reason_stream_with_tools(messages, tools, tool_choice="auto"):
        yield {"type": "delta", "text": "你这指标看着像高血脂，调一调代谢就好。"}

    async def fake_reason(messages):
        # 关键词层放过（无命中短语），但 LLM 复核判 UNSAFE
        return LLMResult(text="UNSAFE：暗示疾病诊断与调理用药")

    monkeypatch.setattr(agent_api, "reason_stream_with_tools", fake_reason_stream_with_tools)
    monkeypatch.setattr(agent_api, "reason", fake_reason)

    token = _token(client)
    r = client.post(
        "/agent/chat",
        json={"message": "帮我看看指标"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    d = _parse_sse(r.text)
    assert d["ok"] is True
    assert "不提供医疗诊断" in d["reply"]   # LLM 复核触发的合规提示
    assert DISCLAIMER_TEXT in d["reply"]    # 免责声明追加


def test_chat_profile_recall_injection_does_not_break(client: TestClient, monkeypatch):
    """M10：即便长期画像召回返回命中，聊天链路仍正常（注入上下文不崩、不泄漏原话）。"""
    from app.modules.memory.service import RecallHit

    async def fake_recall(*_a, **_k):
        return [RecallHit(text="用户讨厌西兰花", score=0.9, source="profile/反思")]

    monkeypatch.setattr(agent_api, "recall_profile", fake_recall)

    token = _token(client)
    r = client.post(
        "/agent/chat",
        json={"message": "今晚吃啥好"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    d = _parse_sse(r.text)
    assert d["ok"] is True
    # 画像注记是 system 上下文，不应原样出现在对用户的最终回复里
    assert "讨厌西兰花" not in d["reply"]


def _fake_image_b64() -> str:
    # 1x1 透明 PNG；fake provider 不解码图像内容，只看提示词决定返回训练/饮食形状
    return "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMCAYAAAGAAAAAASUVORK5CYII="


def _parse_sse_events(text: str) -> list[dict]:
    """把 SSE 文本拆成所有事件帧列表（用于校验特定类型事件是否存在）。"""
    events = []
    for frame in text.split("\n\n"):
        lines = [l for l in frame.split("\n") if l.startswith("data:")]
        if not lines:
            continue
        events.append(json.loads(lines[0][5:].strip()))
    return events


def test_chat_training_image_returns_recognition_card(client: TestClient):
    """M11-2：聊天发训练截图，识别后返回 training_recognition 事件（供前端确认卡，不落库）。"""
    token = _token(client)
    r = client.post(
        "/agent/chat",
        json={"message": "", "image_base64": _fake_image_b64(), "image_mode": "training"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    events = _parse_sse_events(r.text)
    train_ev = next((e for e in events if e.get("type") == "training_recognition"), None)
    assert train_ev is not None, "应推送 training_recognition 事件"
    est = (train_ev.get("recognition") or {}).get("estimate")
    assert est, "应返回非空的训练估算"
    assert "exercise_type" in est


def test_chat_diet_image_default_mode_ok(client: TestClient):
    """回归：默认 diet 模式仍走饮食识别、聊天正常完成（向后兼容旧行为）。"""
    token = _token(client)
    r = client.post(
        "/agent/chat",
        json={"message": "", "image_base64": _fake_image_b64(), "image_mode": "diet"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    d = _parse_sse(r.text)
    assert d["ok"] is True
