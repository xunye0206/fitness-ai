"""限流单测：验证 P1c 登录/注册限流真生效（不放任摆设）。

常规测试会话内限流被 conftest 关闭（共用 127.0.0.1 会误伤），
本文件临时开启并在每个用例前 reset，独立验证阈值行为。
"""
import pytest

from app.core import ratelimit


@pytest.fixture(autouse=True)
def _enable_rl():
    ratelimit.set_enabled(True)
    ratelimit.reset()
    yield
    ratelimit.set_enabled(False)  # 还原 conftest 的关闭状态


def test_register_rate_limit_triggers_429(client):
    # register 阈值 5/60s：前 5 次成功
    for i in range(5):
        r = client.post("/auth/register", json={"username": f"ok{i}", "password": "pw123456"})
        assert r.status_code in (200, 201), r.status_code
    # 第 6 次应被限流
    r6 = client.post("/auth/register", json={"username": "ok6", "password": "pw123456"})
    assert r6.status_code == 429


def test_login_rate_limit_triggers_429(client):
    client.post("/auth/register", json={"username": "loginrl", "password": "pw123456"})
    # 登录失败（错密码）也计限流；login 阈值 10/60s
    for _ in range(10):
        r = client.post("/auth/login", json={"username": "loginrl", "password": "wrong"})
        assert r.status_code == 401, r.status_code
    r11 = client.post("/auth/login", json={"username": "loginrl", "password": "wrong"})
    assert r11.status_code == 429


def test_short_password_rejected(client):
    # P1b：min_length=8 生效
    r = client.post("/auth/register", json={"username": "shortpw", "password": "123"})
    assert r.status_code == 422
