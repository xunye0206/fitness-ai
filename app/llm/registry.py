"""按用途装配 LLM 供应商。

- reasoning / vision / embedding 各自可接不同 API（配置驱动，不写死）。
- provider 名 "fake" 走 FakeProvider（零 key、零网络，用于测试与本地跑通）。
- 其余走 OpenAICompatibleProvider（覆盖 OpenAI/DeepSeek/Qwen/GLM/混元）。
"""
from app.config import get_settings
from app.llm.base import Capability, LLMProvider
from app.llm.providers.fake import FakeProvider

_USE_CAPABILITY = {
    "reasoning": Capability.TEXT,
    "vision": Capability.VISION,
    "embedding": Capability.EMBEDDING,
}


def _openai_provider(use: str, settings) -> LLMProvider:
    from app.llm.providers.openai_compat import OpenAICompatibleProvider

    # provider 名即配置块前缀：deepseek→deepseek_api_key / qwen→qwen_api_key ...
    name = getattr(settings, f"{use}_provider")
    api_key = getattr(settings, f"{name}_api_key", "")
    base_url = getattr(settings, f"{name}_base_url", "")
    default_model = getattr(settings, f"{name}_model", "")
    # 允许按用途单独覆盖模型（如 reasoning 用文本模型、vision 用 VL 模型）
    override_model = getattr(settings, f"{use}_model", "")
    model = override_model or default_model
    capability = _USE_CAPABILITY[use]
    vision_model = getattr(settings, "vision_model", "") or model
    embedding_model = getattr(settings, "embedding_model", "") or model
    return OpenAICompatibleProvider(
        name=name,
        api_key=api_key,
        model=model,
        base_url=base_url,
        capabilities={capability},
        vision_model=vision_model or None,
        embedding_model=embedding_model or None,
    )


def build_providers() -> dict[str, LLMProvider]:
    settings = get_settings()
    providers: dict[str, LLMProvider] = {}
    for use in ("reasoning", "vision"):
        key = getattr(settings, f"{use}_provider", "fake")
        providers[use] = FakeProvider() if key == "fake" else _openai_provider(use, settings)
    emb = getattr(settings, "embedding_provider", "")
    if emb:
        providers["embedding"] = (
            FakeProvider() if emb == "fake" else _openai_provider("embedding", settings)
        )
    return providers


# 模块级单例，避免每次调用重建客户端
_PROVIDERS: dict[str, LLMProvider] = build_providers()


def get(use: str) -> LLMProvider:
    if use not in _PROVIDERS:
        raise KeyError(f"未配置的 LLM 用途：{use}（可用：{list(_PROVIDERS)}）")
    return _PROVIDERS[use]
