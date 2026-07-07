"""OpenAI 兼容供应商适配：一个客户端覆盖 OpenAI / DeepSeek / Qwen / GLM / 混元。

仅 base_url / model / key 不同；视觉走 vision_model，向量走 embedding_model。
懒导入 openai，未装依赖且未启用该 provider 时不报错（M1 默认 fake 不触发）。
失败返回结构化错误，不抛异常，交由调用方降级。
"""
import base64
import json
import logging
from typing import Any, Optional

from app.llm.base import Capability, LLMProvider, LLMResult, Message, ToolCall

logger = logging.getLogger("fitness_agent.llm")


def _detect_mime(b64: str) -> str:
    """按文件头魔数推断图片 MIME（避免把 png/webp 标成 image/jpeg 被拒）。"""
    try:
        head = base64.b64decode(b64[:24])
    except Exception:
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"RIFF") and b"WEBP" in head:
        return "image/webp"
    if head.startswith(b"GIF8"):
        return "image/gif"
    return "image/jpeg"


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        name: str,
        api_key: str,
        model: str,
        base_url: str,
        capabilities: set[Capability],
        vision_model: Optional[str] = None,
        embedding_model: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.capabilities = capabilities
        self.vision_model = vision_model or model
        self.embedding_model = embedding_model or model
        self.timeout = timeout

    def _client(self):
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
        )

    async def reason(
        self,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
        tool_choice: str = "auto",
    ) -> LLMResult:
        if Capability.TEXT not in self.capabilities:
            return LLMResult(text="", ok=False, error="provider 不支持 TEXT")
        try:
            client = self._client()
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": [_msg_to_dict(m) for m in messages],
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice
            resp = await client.chat.completions.create(**payload)
            msg = resp.choices[0].message
            tool_calls: list[ToolCall] = []
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except Exception:
                        args = {}
                    tool_calls.append(
                        ToolCall(id=tc.id, name=tc.function.name, arguments=args)
                    )
            return LLMResult(text=msg.content or "", ok=True, tool_calls=tool_calls)
        except Exception as exc:  # 降级：结构化错误，不抛出
            return LLMResult(text="", ok=False, error=str(exc))

    async def reason_stream(
        self,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
        tool_choice: str = "auto",
    ):
        """流式推理：逐块 yield 文本（str）。

        用于 AI 教练的逐字输出，降低「沉默等待」的体感延迟。
        tool_calls 不在此路径处理（工具决策走非流式的 reason_with_tools）。
        """
        if Capability.TEXT not in self.capabilities:
            yield ""
            return
        try:
            client = self._client()
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": [_msg_to_dict(m) for m in messages],
                "stream": True,
                "stream_options": {"include_usage": False},
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice
            stream = await client.chat.completions.create(**payload)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and getattr(delta, "content", None):
                    yield delta.content
        except Exception:
            # 流式中途出错：yield 空，由调用方在 done 事件标记 ok=False
            yield ""

    async def reason_stream_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        tool_choice: str = "auto",
    ):
        """流式推理并检测工具调用：yield {"type":"delta","text":...} 或 {"type":"tools","calls":[ToolCall]}。

        OpenAI / DeepSeek 兼容接口在 stream 模式下通过 delta.tool_calls 增量回传函数调用，
        这里边收边拼，流结束后若拼接出工具调用则补发一条 tools 事件。
        """
        if Capability.TEXT not in self.capabilities:
            yield {"type": "delta", "text": ""}
            return
        try:
            client = self._client()
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": [_msg_to_dict(m) for m in messages],
                "stream": True,
                "stream_options": {"include_usage": False},
                "tools": tools,
                "tool_choice": tool_choice,
            }
            stream = await client.chat.completions.create(**payload)
            acc: dict[str, dict] = {}  # id -> {"name":..., "args":...}
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and getattr(delta, "content", None):
                    yield {"type": "delta", "text": delta.content}
                for tc in getattr(delta, "tool_calls", []) or []:
                    tid = tc.id or f"_t{len(acc)}"
                    if tid not in acc:
                        acc[tid] = {
                            "name": (tc.function.name if tc.function else None),
                            "args": "",
                        }
                    if tc.function and tc.function.arguments:
                        acc[tid]["args"] += tc.function.arguments
            if acc:
                calls: list[ToolCall] = []
                for tid, info in acc.items():
                    try:
                        args = json.loads(info["args"] or "{}")
                    except Exception:
                        args = {}
                    calls.append(ToolCall(id=tid, name=info["name"], arguments=args))
                yield {"type": "tools", "calls": calls}
        except Exception:
            yield {"type": "delta", "text": ""}

    def _msg_to_dict(m: Message) -> dict:
        """把 Message 序列化为 OpenAI 消息格式；自动带上 tool_calls / tool 角色字段。"""
        d: dict[str, Any] = {"role": m.role, "content": m.content or ""}
        if m.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in m.tool_calls
            ]
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        return d

    async def see(self, image_base64: str, prompt: str) -> LLMResult:
        if Capability.VISION not in self.capabilities:
            return LLMResult(text="", ok=False, error="provider 不支持 VISION")
        mime = _detect_mime(image_base64)
        logger.info("see() 调用视觉API: model=%s, mime=%s, b64_len=%d", self.vision_model, mime, len(image_base64))
        try:
            client = self._client()
            resp = await client.chat.completions.create(
                model=self.vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime};base64,{image_base64}"
                                },
                            },
                        ],
                    }
                ],
            )
            text = resp.choices[0].message.content or ""
            logger.info("see() 视觉API返回成功, text_len=%d", len(text))
            return LLMResult(text=text, ok=True)
        except Exception as exc:
            logger.error("see() 视觉API调用失败: type=%s, msg=%s", type(exc).__name__, exc, exc_info=True)
            return LLMResult(text="", ok=False, error=f"{type(exc).__name__}: {exc}")

    async def embed(self, texts: list[str]) -> LLMResult:
        if Capability.EMBEDDING not in self.capabilities:
            return LLMResult(text="", ok=False, error="provider 不支持 EMBEDDING")
        try:
            client = self._client()
            resp = await client.embeddings.create(model=self.embedding_model, input=texts)
            vectors = [d.embedding for d in resp.data]
            return LLMResult(text="", ok=True, raw={"vectors": vectors})
        except Exception as exc:
            return LLMResult(text="", ok=False, error=str(exc))
