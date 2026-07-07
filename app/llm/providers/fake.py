"""FakeProvider：无 key、无网络的固定结构化返回。

用途：M1 本地跑通链路、单元测试、CI 冒烟。M2 接真实 provider 后此文件不动。
"""
from app.llm.base import Capability, LLMProvider, LLMResult, Message


class FakeProvider(LLMProvider):
    name = "fake"
    capabilities = {Capability.TEXT, Capability.VISION, Capability.EMBEDDING}

    async def reason(self, messages: list[Message], tools=None, tool_choice="auto") -> LLMResult:
        last = messages[-1].content if messages else ""
        return LLMResult(text=f"[fake-reason] 收到指令：{last[:50]}")

    async def see(self, image_base64: str, prompt: str) -> LLMResult:
        estimate = {
            "name": "示例餐食（fake）",
            "calories": 520.0,
            "protein_g": 30.0,
            "carbs_g": 55.0,
            "fat_g": 18.0,
            "confidence": 0.6,
            "note": "测试用固定估算",
        }
        return LLMResult(
            text="[fake-see] 估算：约 520 kcal，蛋白质 30g，碳水 55g，脂肪 18g（置信度 0.6）",
            raw={"estimate": estimate},
        )

    async def embed(self, texts: list[str]) -> LLMResult:
        return LLMResult(text="[fake-embed] ok", raw={"dim": 8, "count": len(texts)})
