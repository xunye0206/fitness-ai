"""report 模块出入参模型（schema 层）。"""
from datetime import datetime

from pydantic import BaseModel


class ReportGenerateRequest(BaseModel):
    """手动触发生成。report_date 留空默认当天；days 控制召回窗口（默认 7）。"""

    report_date: str | None = None
    days: int = 7


class ReportOut(BaseModel):
    id: int
    report_date: str
    summary: str
    advice: str
    model: str
    created_at: datetime
