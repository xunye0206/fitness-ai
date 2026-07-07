"""业务层唯一 LLM 入口：reason / see / embed。

换供应商只改 .env，业务代码此文件以下零改动。
"""
from app.llm.base import LLMResult, Message
from app.llm.registry import get


async def reason(messages: list[Message]) -> LLMResult:
    """文本推理 / 报告生成。走 reasoning_provider。"""
    return await get("reasoning").reason(messages)


async def see(image_base64: str, prompt: str) -> LLMResult:
    """视觉识图（拍照估热量等）。走 vision_provider。"""
    return await get("vision").see(image_base64, prompt)


async def embed(texts: list[str]) -> LLMResult:
    """向量化。走 embedding_provider；未配置时返回结构化失败而非抛错。"""
    try:
        return await get("embedding").embed(texts)
    except KeyError as exc:
        return LLMResult(text="", ok=False, error=str(exc))
