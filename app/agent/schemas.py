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


class TrainingEstimate(BaseModel):
    """视觉识训练截图（Keep/悦跑圈/苹果健身等）的结构化估算结果。

    识别失败时为 None，不抛异常。所有字段均可选：截图看不清的字段留空/0，
    由 confidence 标出可信度，前端据此提示用户确认或修正。
    """

    exercise_type: str = ""          # 运动类型：跑步/骑行/力量训练/游泳…
    duration_min: int = 0            # 时长（分钟）
    calories_burned: float = 0.0     # 消耗热量(kcal)
    distance_km: float = 0.0         # 距离(km)，有氧项目用
    sets: int = 0                    # 组数，力量项目用
    reps: int = 0                    # 每组的次数
    pace: str = ""                   # 配速，如 "5'30\"" / "7:20 /km"
    avg_hr: int = 0                  # 平均心率(bpm)
    intensity: str = "medium"        # low | medium | high
    date: str = ""                   # 训练日期 YYYY-MM-DD（从截图顶部推断，拿不准留空）
    confidence: float = 0.0          # 整体置信度 0-1
    note: str = ""                   # 一句话说明（含看不清的字段提示）
