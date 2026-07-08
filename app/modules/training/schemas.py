"""training 模块出入参模型（schema 层）。"""
from datetime import datetime

from pydantic import BaseModel


class TrainingCreate(BaseModel):
    """用户录入一次训练。date 留空默认当天。"""

    date: str | None = None
    exercise_type: str
    duration_min: int
    intensity: str = "medium"  # low | medium | high
    calories_burned: float = 0.0
    # 截图识别扩展字段（可选）
    distance_km: float = 0.0
    sets: int = 0
    reps: int = 0
    pace: str = ""
    avg_hr: int = 0
    source: str = "manual"  # manual | image | keep
    notes: str = ""


class TrainingOut(BaseModel):
    id: int
    date: str
    exercise_type: str
    duration_min: int
    intensity: str
    calories_burned: float
    distance_km: float = 0.0
    sets: int = 0
    reps: int = 0
    pace: str = ""
    avg_hr: int = 0
    source: str = "manual"
    notes: str = ""
    created_at: datetime
