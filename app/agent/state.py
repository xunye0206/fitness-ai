"""LangGraph 状态定义（饮食 / 训练识别链路）。"""
from typing import Optional, TypedDict

from app.agent.guardrails import GuardrailVerdict
from app.agent.schemas import FoodEstimate, TrainingEstimate


class DietRecognitionState(TypedDict, total=False):
    user_id: int
    image_b64: str
    recognition: Optional[FoodEstimate]
    verdict: Optional[GuardrailVerdict]
    log: list[str]


class TrainingRecognitionState(TypedDict, total=False):
    user_id: int
    image_b64: str
    recognition: Optional[TrainingEstimate]
    verdict: Optional[GuardrailVerdict]
    log: list[str]
