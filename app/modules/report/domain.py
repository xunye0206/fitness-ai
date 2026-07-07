"""report 模块数据模型（domain 层）。
日报由 Agent 生成（基于 7 天召回上下文），经护栏后落库；report 是 Agent 产物的消费者。
注意：报告正文是叙事文本，存 SQLite（属于"报告产物"而非 wiki 叙事记忆）。
wiki 只放画像/反思/洞察，不存逐日报告明细。
"""
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class DailyReport(SQLModel, table=True):
    __tablename__ = "daily_reports"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    # 报告覆盖的日期（默认生成当天）
    report_date: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    summary: str = ""        # 当日/近况小结
    advice: str = ""         # 建议（均附数据依据，非医疗诊断）
    raw_context: str = ""    # 生成时喂给模型的 7 天上下文（可追溯，便于复盘）
    model: str = "fake"      # 实际使用的推理 provider 名
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
