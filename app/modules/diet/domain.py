"""diet 模块数据模型（domain 层）。"""
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
