"""Agent 层共享数据结构（纯 Pydantic，无业务依赖）。"""
from pydantic import BaseModel


class FoodEstimate(BaseModel):
    """视觉识食的结构化估算结果。识别失败时为 None，不抛异常。"""

    name: str = ""
    calories: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    confidence: float = 0.0
    note: str = ""
