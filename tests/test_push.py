"""push 模块测试：限流、鉴权、固定推送、事件巡检、护栏拦截。

全程用 FakeProvider，不联网、不依赖真实 API key。
"""
import pytest


def _auth_headers(client, username="pushuser"):
    r = client.post(
        "/auth/register",
        json={"username": username, "password": "pw123456"},
    )
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_push_list_requires_auth(client):
    # 不上 token 必须 401
    r = client.get("/push")
    assert r.status_code == 401


def test_push_trigger_creates_message(client):
    h = _auth_headers(client, "p1")
    r = client.post("/push/trigger", json={"event_type": "morning_nudge"}, headers=h)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "sent"
    assert body["event_type"] == "morning_nudge"
    assert body["blocked_reason"] is None


def test_push_evening_recap_has_disclaimer(client):
    h = _auth_headers(client, "p2")
    r = client.post("/push/trigger", json={"event_type": "evening_recap"}, headers=h)
    assert r.status_code == 201
    assert "非医疗诊断" in r.json()["body"]


def test_push_rate_limit(client):
    h = _auth_headers(client, "p3")
    # 前 3 条应成功
    for i in range(3):
        r = client.post(
            "/push/trigger", json={"event_type": f"morning_nudge_{i}"}, headers=h
        )
        assert r.status_code == 201
        assert r.json()["status"] == "sent"
    # 第 4 条被限流，落库为 blocked
    r4 = client.post("/push/trigger", json={"event_type": "morning_nudge_x"}, headers=h)
    assert r4.status_code == 201
    assert r4.json()["status"] == "blocked"
    assert "上限" in r4.json()["blocked_reason"]


def test_push_event_abandon_warning_fires(client):
    # 全新用户、无任何记录 → 巡检应触发放弃预警
    h = _auth_headers(client, "p4")
    r = client.post("/push/scan", json={}, headers=h)
    assert r.status_code == 200
    assert r.json()["fired"] >= 1
    # 列表里应有 abandon_warning 且已送达
    lst = client.get("/push", headers=h).json()
    types = [m["event_type"] for m in lst]
    assert "abandon_warning" in types
    aw = next(m for m in lst if m["event_type"] == "abandon_warning")
    assert aw["status"] == "sent"
    assert "非医疗诊断" in aw["body"]


def test_push_event_no_fire_when_active(client):
    # 今天有训练记录 → 不应触发放弃预警
    h = _auth_headers(client, "p5")
    tr = client.post(
        "/training",
        json={"exercise_type": "跑步", "duration_min": 30, "calories_burned": 200},
        headers=h,
    )
    assert tr.status_code == 201
    r = client.post("/push/scan", json={}, headers=h)
    assert r.status_code == 200
    assert r.json()["fired"] == 0


def test_push_guardrail_blocks_injury_add(client):
    # 训练备注含伤病信号 → 含「加练」的推送被拦截
    h = _auth_headers(client, "p6")
    tr = client.post(
        "/training",
        json={"exercise_type": "力量", "duration_min": 40, "notes": "膝盖有点疼"},
        headers=h,
    )
    assert tr.status_code == 201
    r = client.post(
        "/push/trigger",
        json={"event_type": "morning_nudge", "body": "今天加练一小时突破极限"},
        headers=h,
    )
    assert r.status_code == 201
    assert r.json()["status"] == "blocked"
    assert "伤病" in r.json()["blocked_reason"]
