"""diet 模块数据模型（domain 层）。"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

# 统一时区基准：北京时间（东八区）。个人项目，用户在中国，时间展示以此为准。
CST = timezone(timedelta(hours=8))


class DietEntry(SQLModel, table=True):
    __tablename__ = "diet_entries"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    image_path: str | None = None
    name: str = ""
    calories: float = 0.0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    confidence: float = 0.0
    status: str = "pending"  # pending（待确认）| confirmed（已确认）
    raw_estimate: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True)),  # 保留时区，避免入库时被当本地 naive 丢时区
    )
    meal_type: str = Field(default="other")  # 餐次：breakfast/lunch/afternoon_tea/dinner/midnight_snack/other
