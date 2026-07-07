"""diet 模块出入参模型（schema 层）。"""
from datetime import datetime

from app.agent.schemas import FoodEstimate
from pydantic import BaseModel


class CorrectRequest(BaseModel):
    """用户修正/确认饮食估算。只传要改的字段，其余保留 AI 估算。"""

    name: str | None = None
    calories: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None


class RecognizeResponse(BaseModel):
    entry_id: int
    estimate: FoodEstimate | None = None
    needs_confirmation: bool = False
    guardrail_reasons: list[str] = []


class DietEntryOut(BaseModel):
    id: int
    name: str
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    confidence: float
    status: str
    meal_type: str = "other"
    created_at: datetime
