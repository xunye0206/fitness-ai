"""FakeProvider：无 key、无网络的固定结构化返回。

用途：M1 本地跑通链路、单元测试、CI 冒烟。M2 接真实 provider 后此文件不动。
"""
import asyncio

from app.llm.base import Capability, LLMProvider, LLMResult, Message


class FakeProvider(LLMProvider):
    name = "fake"
    capabilities = {Capability.TEXT, Capability.VISION, Capability.EMBEDDING}

    async def reason(self, messages: list[Message], tools=None, tool_choice="auto") -> LLMResult:
        last = messages[-1].content if messages else ""
        return LLMResult(text=f"[fake-reason] 收到指令：{last[:50]}")

    async def reason_stream(self, messages: list[Message], tools=None, tool_choice="auto"):
        """流式版：把固定文本按句切分 yield（测试用）。"""
        text = f"[fake-reason] 收到指令：{messages[-1].content[:50] if messages else ''}"
        for seg in text.split("，"):
            yield seg + ("，" if not seg.endswith("。") else "")
            await asyncio.sleep(0)

    async def reason_stream_with_tools(self, messages: list[Message], tools, tool_choice="auto"):
        """流式 + 工具检测（测试用）：直接把固定文本作为 delta 推完，不带工具调用。"""
        text = f"[fake-reason] 收到指令：{messages[-1].content[:50] if messages else ''}"
        yield {"type": "delta", "text": text}

    async def see(self, image_base64: str, prompt: str) -> LLMResult:
        # 提示词请求训练结构化字段时，返回训练形状的固定估算（保持 training 链路可测）
        if "exercise_type" in prompt or "训练" in prompt:
            estimate = {
                "exercise_type": "跑步（fake）",
                "duration_min": 30,
                "calories_burned": 280.0,
                "distance_km": 5.0,
                "sets": 0,
                "reps": 0,
                "pace": "6:00",
                "avg_hr": 150,
                "intensity": "medium",
                "date": "",
                "confidence": 0.7,
                "note": "测试用固定训练估算",
            }
            return LLMResult(
                text="[fake-see] 训练：跑步 30 分钟，5.0km，约 280 kcal（置信度 0.7）",
                raw={"estimate": estimate},
            )
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
