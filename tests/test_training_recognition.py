"""M11 训练截图识别测试：上传→视觉解析→确认保存→字段落库。全程 fake 模式不联网。

对应 diet 识别测试（test_diet.py），验证 training 版同构链路可用、新字段能正确往返。
"""
import base64

from app.agent.graph_training import parse_training_estimate
from app.agent.schemas import TrainingEstimate
from app.llm.base import LLMResult

# 1x1 透明 PNG，合法图片，足以走完上传链路
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
PNG_BYTES = base64.b64decode(PNG_B64)


def _auth_headers(client) -> dict:
    r = client.post("/auth/register", json={"username": "train1", "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_recognize_requires_auth(client):
    r = client.post("/training/recognize", files={"image": ("a.png", PNG_BYTES, "image/png")})
    assert r.status_code == 401


def test_recognize_returns_structured_estimate(client):
    h = _auth_headers(client)
    r = client.post(
        "/training/recognize", files={"image": ("a.png", PNG_BYTES, "image/png")}, headers=h
    )
    assert r.status_code == 200
    body = r.json()
    est = body["estimate"]
    assert est is not None
    # fake 训练估算字段
    assert est["exercise_type"] == "跑步（fake）"
    assert est["duration_min"] == 30
    assert est["distance_km"] == 5.0
    assert est["calories_burned"] == 280.0
    assert est["avg_hr"] == 150
    # fake 置信度 0.7 > 阈值 0.3 → 不需确认
    assert body["needs_confirmation"] is False


def test_recognize_then_confirm_saves_with_new_fields(client):
    h = _auth_headers(client)
    r = client.post(
        "/training/recognize", files={"image": ("a.png", PNG_BYTES, "image/png")}, headers=h
    )
    est = r.json()["estimate"]
    # 前端确认：把识别结果（可编辑）POST 到 /training 保存
    payload = {
        "exercise_type": est["exercise_type"],
        "duration_min": est["duration_min"],
        "intensity": est["intensity"],
        "calories_burned": est["calories_burned"],
        "distance_km": est["distance_km"],
        "sets": est["sets"],
        "reps": est["reps"],
        "pace": est["pace"],
        "avg_hr": est["avg_hr"],
        "source": "image",
        "notes": "由截图识别导入",
    }
    r2 = client.post("/training", json=payload, headers=h)
    assert r2.status_code == 201
    saved = r2.json()
    assert saved["distance_km"] == 5.0
    assert saved["avg_hr"] == 150
    assert saved["source"] == "image"

    # 列表里能查到，且新字段保留
    r3 = client.get("/training", headers=h)
    assert r3.status_code == 200
    items = r3.json()
    assert any(i["id"] == saved["id"] and i["distance_km"] == 5.0 for i in items)


def test_parse_training_estimate_from_raw():
    raw = {
        "exercise_type": "力量训练",
        "duration_min": 45,
        "calories_burned": 220.0,
        "distance_km": 0.0,
        "sets": 4,
        "reps": 12,
        "pace": "",
        "avg_hr": 130,
        "intensity": "high",
        "date": "2026-07-08",
        "confidence": 0.9,
        "note": "力量训练，看不清组间休息",
    }
    res = LLMResult(text="", raw={"estimate": raw})
    est = parse_training_estimate(res)
    assert isinstance(est, TrainingEstimate)
    assert est.exercise_type == "力量训练"
    assert est.sets == 4 and est.reps == 12
    assert est.intensity == "high"
