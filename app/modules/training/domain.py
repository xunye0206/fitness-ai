"""training 模块数据模型（domain 层）。"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime
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
    # 以下为「截图识别」扩展字段（可选，手动录入时多为空）：
    distance_km: float = 0.0       # 距离(km)，有氧项目
    sets: int = 0                  # 组数，力量项目
    reps: int = 0                  # 每组次数
    pace: str = ""                 # 配速，如 "6:00"
    avg_hr: int = 0                # 平均心率(bpm)
    source: str = "manual"         # manual | image | keep（区分录入来源，便于分析）
    notes: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True)),
    )
