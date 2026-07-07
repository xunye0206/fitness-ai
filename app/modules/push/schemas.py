"""push 模块出入参。"""
from datetime import datetime

from pydantic import BaseModel


class PushOut(BaseModel):
    id: int
    user_id: int
    event_type: str
    title: str
    body: str
    channel: str
    status: str
    blocked_reason: str | None = None
    created_at: datetime


class PushTriggerIn(BaseModel):
    """手动触发一条推送（测试 / 调试用）。不传 title/body 时用该事件类型的默认模板。"""
    event_type: str
    title: str | None = None
    body: str | None = None


class PushScanOut(BaseModel):
    """事件巡检结果。"""
    scanned: int
    fired: int
