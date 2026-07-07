def test_register_login_me_flow(client):
    # 注册
    r = client.post("/auth/register", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 201
    token = r.json()["access_token"]
    assert token

    # 重复注册应冲突
    r2 = client.post("/auth/register", json={"username": "alice", "password": "secret123"})
    assert r2.status_code == 409

    # 登录
    r3 = client.post("/auth/login", json={"username": "alice", "password": "secret123"})
    assert r3.status_code == 200
    token2 = r3.json()["access_token"]

    # 错误密码应 401
    r4 = client.post("/auth/login", json={"username": "alice", "password": "wrong"})
    assert r4.status_code == 401

    # /me 带令牌
    r5 = client.get("/auth/me", headers={"Authorization": f"Bearer {token2}"})
    assert r5.status_code == 200
    body = r5.json()
    assert body["username"] == "alice"
    assert "password_hash" not in body  # 不能泄露敏感字段

    # /me 无令牌应 401
    r6 = client.get("/auth/me")
    assert r6.status_code == 401
