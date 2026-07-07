"""饮食识别的 LangGraph 状态机：vision → guardrail → finalize。

- 业务层（diet.service）只调 run_diet_recognition，不直接碰 LLM。
- 护栏在 finalize 之前的前置节点执行（护栏前置拦截）。
- recursion_limit 充当 max_steps，防止状态机死循环。
"""
import json
import re

from langgraph.graph import END, START, StateGraph

from app.agent.guardrails import Guardrails
from app.agent.schemas import FoodEstimate
from app.agent.state import DietRecognitionState
from app.llm.base import LLMResult
from app.llm.router import see

VISION_PROMPT = (
    "请识别这张食物图片，估算其热量(kcal)与宏量营养素"
    "(蛋白质/碳水/脂肪, 单位g)、置信度(0-1)，并给一句说明。"
    "只返回一个 JSON 对象，不要任何额外文字，字段如下："
    '{"name":"食物名","calories":0,"protein_g":0,"carbs_g":0,"fat_g":0,"confidence":0,"note":"一句话说明"}'
)

_guardrails = Guardrails()


def _extract_json(text: str):
    """从模型文本里稳健抽取第一个 JSON 对象（兼容 ```json 围栏与前后杂文）。"""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            pass
    return None


def parse_estimate(result: LLMResult) -> FoodEstimate | None:
    """把 LLM 返回解析成结构化估算；优先用 raw.estimate，其次从文本抽取 JSON。"""
    if result.raw and isinstance(result.raw.get("estimate"), dict):
        try:
            return FoodEstimate(**result.raw["estimate"])
        except Exception:
            return None
    obj = _extract_json(result.text) if isinstance(result.text, str) else None
    if isinstance(obj, dict):
        try:
            return FoodEstimate(
                name=str(obj.get("name", "")),
                calories=float(obj.get("calories") or 0),
                protein_g=float(obj.get("protein_g") or 0),
                carbs_g=float(obj.get("carbs_g") or 0),
                fat_g=float(obj.get("fat_g") or 0),
                confidence=float(obj.get("confidence") or 0),
                note=str(obj.get("note", "") or ""),
            )
        except Exception:
            return None
    return None


async def vision_node(state: DietRecognitionState) -> dict:
    result = await see(state["image_b64"], VISION_PROMPT)
    return {"recognition": parse_estimate(result), "log": [f"vision ok={result.ok}"]}


async def guardrail_node(state: DietRecognitionState) -> dict:
    verdict = _guardrails.evaluate(state.get("recognition"))
    return {"verdict": verdict, "log": [f"guardrail needs_confirmation={verdict.needs_confirmation}"]}


async def finalize_node(_state: DietRecognitionState) -> dict:
    return {"log": ["finalize done"]}


def build_diet_graph():
    g = StateGraph(DietRecognitionState)
    g.add_node("vision", vision_node)
    g.add_node("guardrail", guardrail_node)
    g.add_node("finalize", finalize_node)
    g.add_edge(START, "vision")
    g.add_edge("vision", "guardrail")
    g.add_edge("guardrail", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


async def run_diet_recognition(user_id: int, image_b64: str) -> dict:
    graph = build_diet_graph()
    initial: DietRecognitionState = {
        "user_id": user_id,
        "image_b64": image_b64,
        "recognition": None,
        "verdict": None,
        "log": [],
    }
    return await graph.ainvoke(initial, config={"recursion_limit": 10})
