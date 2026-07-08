"""训练截图识别的 LangGraph 状态机：vision → guardrail → finalize。

- 与 diet 识别同构（见 graph.py）。业务层（training.service）只调 run_training_recognition。
- 提示词针对健身 App 截图（Keep/悦跑圈/苹果健身/Garmin 等）调过：要求只回结构化 JSON，
  看不清的字段填 0/空并在 note 指出，由 confidence 标出整体可信度。
- 护栏与 diet 共用（只看 confidence 阈值），低于阈值标记 needs_confirmation。
"""
import json
import logging
import re

from langgraph.graph import END, START, StateGraph

from app.agent.guardrails import Guardrails
from app.agent.schemas import TrainingEstimate
from app.agent.state import TrainingRecognitionState
from app.llm.base import LLMResult
from app.llm.router import see

logger = logging.getLogger("fitness_agent.vision")

VISION_PROMPT = (
    "请识别这张健身 App（如 Keep、悦跑圈、苹果健身、Garmin 等）的训练截图，"
    "提取本次训练的数据。只返回一个 JSON 对象，不要任何额外文字，字段如下："
    '{"exercise_type":"运动类型(如 跑步/骑行/力量训练/游泳)","duration_min":0,'
    '"calories_burned":0,"distance_km":0,"sets":0,"reps":0,'
    '"pace":"配速 mm:ss/km，如 5:30，没有则留空","avg_hr":0,'
    '"intensity":"low|medium|high","date":"YYYY-MM-DD","confidence":0,'
    '"note":"一句话说明，把看不清/不确定的字段在这里指出"}'
    "注意：截图顶部通常显示训练日期，请尽量推断 date；"
    "任何看不清的字段填 0 或空字符串，并在 note 中说明；confidence 表示你整体的把握(0-1)。"
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


def parse_training_estimate(result: LLMResult) -> TrainingEstimate | None:
    """把 LLM 返回解析成结构化训练估算；优先用 raw.estimate，其次从文本抽取 JSON。"""
    if result.raw and isinstance(result.raw.get("estimate"), dict):
        try:
            return TrainingEstimate(**result.raw["estimate"])
        except Exception:
            return None
    obj = _extract_json(result.text) if isinstance(result.text, str) else None
    if isinstance(obj, dict):
        try:
            return TrainingEstimate(
                exercise_type=str(obj.get("exercise_type", "")),
                duration_min=int(obj.get("duration_min") or 0),
                calories_burned=float(obj.get("calories_burned") or 0),
                distance_km=float(obj.get("distance_km") or 0),
                sets=int(obj.get("sets") or 0),
                reps=int(obj.get("reps") or 0),
                pace=str(obj.get("pace", "") or ""),
                avg_hr=int(obj.get("avg_hr") or 0),
                intensity=str(obj.get("intensity") or "medium"),
                date=str(obj.get("date", "") or ""),
                confidence=float(obj.get("confidence") or 0),
                note=str(obj.get("note", "") or ""),
            )

        except Exception:
            return None
    return None


async def vision_node(state: TrainingRecognitionState) -> dict:
    b64_len = len(state["image_b64"] or "")
    logger.info("training vision_node 开始，base64 长度=%d", b64_len)
    try:
        result = await see(state["image_b64"], VISION_PROMPT)
    except Exception as exc:
        logger.error("vision API 调用异常: type=%s, msg=%s", type(exc).__name__, exc, exc_info=True)
        return {"recognition": None, "log": [f"vision 异常: {type(exc).__name__}: {exc}"]}
    if not result.ok:
        logger.warning("vision API 返回 ok=False, error=%s", result.error)
        return {"recognition": None, "log": [f"vision 失败: {result.error}"]}
    est = parse_training_estimate(result)
    if est is None:
        logger.warning("vision 返回文本但无法解析为 TrainingEstimate, text=%s", (result.text or "")[:200])
        return {"recognition": None, "log": [f"vision 解析失败, 原始文本: {(result.text or '')[:200]}"]}
    logger.info("training vision 识别成功: type=%s, dur=%d", est.exercise_type, est.duration_min)
    return {"recognition": est, "log": [f"vision ok={result.ok}"]}


async def guardrail_node(state: TrainingRecognitionState) -> dict:
    verdict = _guardrails.evaluate(state.get("recognition"))
    return {"verdict": verdict, "log": [f"guardrail needs_confirmation={verdict.needs_confirmation}"]}


async def finalize_node(_state: TrainingRecognitionState) -> dict:
    return {"log": ["finalize done"]}


def build_training_graph():
    g = StateGraph(TrainingRecognitionState)
    g.add_node("vision", vision_node)
    g.add_node("guardrail", guardrail_node)
    g.add_node("finalize", finalize_node)
    g.add_edge(START, "vision")
    g.add_edge("vision", "guardrail")
    g.add_edge("guardrail", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


async def run_training_recognition(user_id: int, image_b64: str) -> dict:
    graph = build_training_graph()
    initial: TrainingRecognitionState = {
        "user_id": user_id,
        "image_b64": image_b64,
        "recognition": None,
        "verdict": None,
        "log": [],
    }
    return await graph.ainvoke(initial, config={"recursion_limit": 10})
