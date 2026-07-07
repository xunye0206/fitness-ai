"""OpenAI 兼容供应商适配：一个客户端覆盖 OpenAI / DeepSeek / Qwen / GLM / 混元。

仅 base_url / model / key 不同；视觉走 vision_model，向量走 embedding_model。
懒导入 openai，未装依赖且未启用该 provider 时不报错（M1 默认 fake 不触发）。
失败返回结构化错误，不抛异常，交由调用方降级。
"""
from typing import Any, Optional

from app.llm.base import Capability, LLMProvider, LLMResult, Message


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

    async def reason(self, messages: list[Message]) -> LLMResult:
        if Capability.TEXT not in self.capabilities:
            return LLMResult(text="", ok=False, error="provider 不支持 TEXT")
        try:
            client = self._client()
            resp = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            )
            return LLMResult(text=resp.choices[0].message.content or "", ok=True)
        except Exception as exc:  # 降级：结构化错误，不抛出
            return LLMResult(text="", ok=False, error=str(exc))

    async def see(self, image_base64: str, prompt: str) -> LLMResult:
        if Capability.VISION not in self.capabilities:
            return LLMResult(text="", ok=False, error="provider 不支持 VISION")
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
                                    "url": f"data:image/jpeg;base64,{image_base64}"
                                },
                            },
                        ],
                    }
                ],
            )
            return LLMResult(text=resp.choices[0].message.content or "", ok=True)
        except Exception as exc:
            return LLMResult(text="", ok=False, error=str(exc))

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
