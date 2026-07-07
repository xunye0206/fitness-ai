"""LLM 路由测试：验证 provider 名正确映射到对应配置块的 key/model（不联网）。

回归：早期 registry 误读 {use}_api_key（不存在），导致接真模型时 key 为空。
本测试确保 reasoning=deepseek 用 DEEPSEEK_*，vision=qwen 用 QWEN_*。
"""
import pytest


@pytest.fixture
def _route_env(monkeypatch):
    for k in [
        "REASONING_PROVIDER", "VISION_PROVIDER", "EMBEDDING_PROVIDER",
        "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL",
        "QWEN_API_KEY", "QWEN_MODEL", "QWEN_BASE_URL",
    ]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REASONING_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deep")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("VISION_PROVIDER", "qwen")
    monkeypatch.setenv("QWEN_API_KEY", "sk-qwen")
    monkeypatch.setenv("QWEN_MODEL", "qwen-vl-max")
    monkeypatch.setenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    from app.config import get_settings

    get_settings.cache_clear()


def test_routing_reads_correct_provider_keys(_route_env):
    from app.llm.registry import build_providers

    providers = build_providers()
    r = providers["reasoning"]
    assert r.api_key == "sk-deep"
    assert r.model == "deepseek-chat"
    assert r.base_url == "https://api.deepseek.com/v1"

    v = providers["vision"]
    assert v.api_key == "sk-qwen"
    assert v.vision_model == "qwen-vl-max"
    assert v.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_routing_fake_when_provider_is_fake(_route_env, monkeypatch):
    from app.llm.registry import build_providers
    from app.llm.providers.fake import FakeProvider

    monkeypatch.setenv("REASONING_PROVIDER", "fake")
    monkeypatch.setenv("VISION_PROVIDER", "fake")
    from app.config import get_settings

    get_settings.cache_clear()
    providers = build_providers()
    assert isinstance(providers["reasoning"], FakeProvider)
    assert isinstance(providers["vision"], FakeProvider)
