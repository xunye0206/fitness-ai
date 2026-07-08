"""M8：AI 教练对话接口测试（fake 模型，零网络零 key）。

端点改为 SSE 流式输出，测试解析 SSE 帧聚合成 {reply, actions, ok}。
"""
import json

from app.agent import api as agent_api
from app.agent.guardrails import DISCLAIMER_TEXT
from app.llm.base import LLMResult
from app.main import app
from fastapi.testclient import TestClient


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
