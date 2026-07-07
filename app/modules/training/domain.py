"""training 模块数据模型（domain 层）。"""
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class TrainingEntry(SQLModel, table=True):
    __tablename__ = "training_entries"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    # 训练日期（可填过去某天，默认当天）。仅日期部分用于聚合，不存时分秒。
    date: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    exercise_type: str = ""  # 如 跑步 / 力量 / 骑行
    duration_min: int = 0
    intensity: str = "medium"  # low | medium | high
    calories_burned: float = 0.0
    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
