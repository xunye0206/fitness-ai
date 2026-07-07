"""M8：AI 教练对话接口测试（fake 模型，零网络零 key）。"""
from app.main import app
from fastapi.testclient import TestClient


def _token(client: TestClient) -> str:
    r = client.post("/auth/register", json={"username": "coachtester", "password": "secret123"})
    return r.json()["access_token"]


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
    d = r.json()
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
    assert r.json()["ok"] is True
