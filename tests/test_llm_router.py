"""LLM 路由冒烟：默认 fake provider，全程不联网。"""
import asyncio

from app.llm.base import Capability, Message
from app.llm.registry import get
from app.llm.router import embed, reason, see


def test_reason_fake():
    result = asyncio.run(reason([Message("user", "生成今日健身建议")]))
    assert result.ok
    assert "fake-reason" in result.text


def test_see_fake():
    result = asyncio.run(see("base64xxxx", "估算这张食物的热量"))
    assert result.ok
    assert "fake-see" in result.text


def test_embed_disabled_by_default():
    # 默认 embedding_provider 为空 -> 应返回结构化失败而非抛错
    result = asyncio.run(embed(["hello"]))
    assert result.ok is False
    assert "embedding" in result.error


def test_fake_provider_capabilities():
    p = get("reasoning")
    assert Capability.TEXT in p.capabilities
    assert Capability.VISION in p.capabilities
