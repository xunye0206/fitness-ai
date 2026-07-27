"""push 模块数据模型（domain 层）。"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class PushMessage(SQLModel, table=True):
    __tablename__ = "push_messages"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    # 事件类型：morning_nudge（晨间提醒）| evening_recap（晚间回顾）| abandon_warning（放弃预警）
    event_type: str
    title: str
    body: str
    channel: str = "in_app"  # in_app（一期）| wechat（二期）
    # 状态：sent（已送达）| blocked（被护栏/限流拦截）
    status: str = "sent"
    blocked_reason: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True)),
    )
