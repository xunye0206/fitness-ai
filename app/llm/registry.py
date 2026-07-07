"""按用途装配 LLM 供应商。

- reasoning / vision / embedding 各自可接不同 API（配置驱动，不写死）。
- provider 名 "fake" 走 FakeProvider（零 key、零网络，用于测试与本地跑通）。
- 其余走 OpenAICompatibleProvider（覆盖 OpenAI/DeepSeek/Qwen/GLM/混元）。
"""
from app.config import settings
from app.llm.base import Capability, LLMProvider
from app.llm.providers.fake import FakeProvider

_USE_CAPABILITY = {
    "reasoning": Capability.TEXT,
    "vision": Capability.VISION,
    "embedding": Capability.EMBEDDING,
}


def _openai_provider(use: str) -> LLMProvider:
    from app.llm.providers.openai_compat import OpenAICompatibleProvider

    name = getattr(settings, f"{use}_provider")
    api_key = getattr(settings, f"{use}_api_key", "")
    base_url = getattr(settings, f"{use}_base_url", "")
    model = getattr(settings, f"{use}_model", "")
    capability = _USE_CAPABILITY[use]
    return OpenAICompatibleProvider(
        name=name,
        api_key=api_key,
        model=model,
        base_url=base_url,
        capabilities={capability},
    )


def build_providers() -> dict[str, LLMProvider]:
    providers: dict[str, LLMProvider] = {}
    for use in ("reasoning", "vision"):
        key = getattr(settings, f"{use}_provider", "fake")
        providers[use] = FakeProvider() if key == "fake" else _openai_provider(use)
    emb = getattr(settings, "embedding_provider", "")
    if emb:
        providers["embedding"] = (
            FakeProvider() if emb == "fake" else _openai_provider("embedding")
        )
    return providers


# 模块级单例，避免每次调用重建客户端
_PROVIDERS: dict[str, LLMProvider] = build_providers()


def get(use: str) -> LLMProvider:
    if use not in _PROVIDERS:
        raise KeyError(f"未配置的 LLM 用途：{use}（可用：{list(_PROVIDERS)}）")
    return _PROVIDERS[use]
