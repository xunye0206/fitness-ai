"""M3 训练录入垂直切片测试：鉴权、录入、列表。全程不联网不填 key。"""
from tests.test_diet import _auth_headers


def test_training_requires_auth(client):
    r = client.post("/training", json={"exercise_type": "跑步", "duration_min": 30})
    assert r.status_code == 401


def test_record_and_list_training(client):
    h = _auth_headers(client)
    r = client.post(
        "/training",
        json={"exercise_type": "跑步", "duration_min": 30, "intensity": "high",
              "calories_burned": 280.0, "notes": "晨跑"},
        headers=h,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["exercise_type"] == "跑步"
    assert body["duration_min"] == 30
    assert body["date"]  # 默认填了当天

    r2 = client.get("/training", headers=h)
    assert r2.status_code == 200
    assert isinstance(r2.json(), list)
    assert len(r2.json()) >= 1


def test_training_isolated_per_user(client):
    # 注意：_auth_headers 固定用用户名 "diet1"，重复注册会 409；这里手动用不同用户名
    r1 = client.post("/auth/register", json={"username": "tuser1", "password": "pw123456"})
    h1 = {"Authorization": f"Bearer {r1.json()['access_token']}"}
    r2 = client.post("/auth/register", json={"username": "tuser2", "password": "pw123456"})
    h2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    client.post("/training", json={"exercise_type": "力量", "duration_min": 45}, headers=h1)
    r = client.get("/training", headers=h2)
    assert r.status_code == 200
    assert r.json() == []  # 用户2看不到用户1的记录
