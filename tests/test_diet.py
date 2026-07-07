"""M2 饮食垂直切片测试：识别→修正→列表 + 护栏单测。全程不联网不填 key。"""
import base64

from app.agent.guardrails import Guardrails
from app.agent.graph import _extract_json, parse_estimate
from app.agent.schemas import FoodEstimate
from app.llm.base import LLMResult

# 1x1 透明 PNG，合法图片，足以走完上传链路
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
PNG_BYTES = base64.b64decode(PNG_B64)


def _auth_headers(client) -> dict:
    r = client.post("/auth/register", json={"username": "diet1", "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_recognize_requires_auth(client):
    r = client.post("/diet/recognize", files={"image": ("a.png", PNG_BYTES, "image/png")})
    assert r.status_code == 401


def test_recognize_and_correct_flow(client):
    h = _auth_headers(client)
    r = client.post(
        "/diet/recognize", files={"image": ("a.png", PNG_BYTES, "image/png")}, headers=h
    )
    assert r.status_code == 201
    body = r.json()
    assert "entry_id" in body
    assert body["estimate"]["calories"] == 520.0
    assert body["needs_confirmation"] is False  # fake 置信度 0.6 > 阈值 0.3

    eid = body["entry_id"]
    r2 = client.post(
        f"/diet/{eid}/correct",
        json={"calories": 600.0, "name": "鸡胸肉餐"},
        headers=h,
    )
    assert r2.status_code == 200
    assert r2.json()["calories"] == 600.0
    assert r2.json()["status"] == "confirmed"


def test_list_diet(client):
    h = _auth_headers(client)
    client.post("/diet/recognize", files={"image": ("a.png", PNG_BYTES, "image/png")}, headers=h)
    r = client.get("/diet", headers=h)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_guardrail_low_confidence_needs_confirmation():
    low = FoodEstimate(name="x", calories=100, confidence=0.1)
    v = Guardrails().evaluate(low)
    assert v.needs_confirmation is True
    assert any("置信度" in reason for reason in v.reasons)


def test_guardrail_none_blocked():
    v = Guardrails().evaluate(None)
    assert v.allowed is False


# ---- 真模型 JSON 解析（不联网，验证 qwen 自然语言→结构化估算的解析链路）----
def test_parse_estimate_from_fenced_json():
    text = '```json\n{"name":"苹果","calories":95,"protein_g":0.5,"carbs_g":25,"fat_g":0.3,"confidence":0.9,"note":"中等苹果"}\n```'
    est = parse_estimate(LLMResult(text=text, ok=True))
    assert est is not None
    assert est.name == "苹果"
    assert est.calories == 95.0
    assert est.confidence == 0.9


def test_parse_estimate_from_inline_json():
    text = '识别结果：{"name":"鸡胸肉","calories":165,"protein_g":31,"carbs_g":0,"fat_g":3.6,"confidence":0.85,"note":"100g"} 仅供参考'
    est = parse_estimate(LLMResult(text=text, ok=True))
    assert est is not None
    assert est.name == "鸡胸肉"
    assert est.protein_g == 31.0


def test_parse_estimate_no_json_returns_none():
    est = parse_estimate(LLMResult(text="这张图片不是食物，无法识别", ok=True))
    assert est is None


def test_extract_json_handles_garbage():
    assert _extract_json("") is None
    assert _extract_json("no json here") is None
