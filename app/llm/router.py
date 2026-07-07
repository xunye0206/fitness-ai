"""业务层唯一 LLM 入口：reason / see / embed。

换供应商只改 .env，业务代码此文件以下零改动。
"""
from typing import Optional

from app.llm.base import LLMResult, Message
from app.llm.registry import get


async def reason(messages: list[Message]) -> LLMResult:
    """文本推理 / 报告生成。走 reasoning_provider。"""
    return await get("reasoning").reason(messages)


async def reason_with_tools(
    messages: list[Message],
    tools: list[dict],
    tool_choice: str = "auto",
) -> LLMResult:
    """带函数调用的文本推理（agent 工具循环用）。走 reasoning_provider。

    provider 不支持 tools 时由 provider 内部降级返回 ok=False，调用方自行兜底。
    """
    provider = get("reasoning")
    try:
        return await provider.reason(messages, tools=tools, tool_choice=tool_choice)
    except Exception as exc:
        return LLMResult(text="", ok=False, error=str(exc))


async def reason_stream(messages: list[Message], tools: Optional[list[dict]] = None):
    """流式文本推理（逐字输出）。走 reasoning_provider，yield 文本片段。

    工具决策请仍用 reason_with_tools / reason_stream_with_tools（带 tool_calls 解析）。
    """
    provider = get("reasoning")
    try:
        async for chunk in provider.reason_stream(messages, tools=tools):
            yield chunk
    except Exception:
        yield ""


async def reason_stream_with_tools(messages: list[Message], tools: list[dict], tool_choice: str = "auto"):
    """流式推理并检测工具调用：yield {"type":"delta","text":...} 或 {"type":"tools","calls":[ToolCall]}。

    调用方在收到 delta 时即可推给前端（首 token 极早到达）；收到 tools 事件时执行工具并回填，
    再调用 reason_stream 生成最终回复。
    """
    provider = get("reasoning")
    try:
        async for ev in provider.reason_stream_with_tools(messages, tools, tool_choice):
            yield ev
    except Exception:
        yield {"type": "delta", "text": ""}


async def see(image_base64: str, prompt: str) -> LLMResult:
    """视觉识图（拍照估热量等）。走 vision_provider。"""
    return await get("vision").see(image_base64, prompt)


async def embed(texts: list[str]) -> LLMResult:
    """向量化。走 embedding_provider；未配置时返回结构化失败而非抛错。"""
    try:
        return await get("embedding").embed(texts)
    except KeyError as exc:
        return LLMResult(text="", ok=False, error=str(exc))
