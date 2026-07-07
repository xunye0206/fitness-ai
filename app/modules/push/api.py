"""push 模块路由（api 层）。只做鉴权、参数校验、调 service、返回。"""
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.modules.auth.api import get_current_user
from app.modules.auth.domain import User
from app.modules.push.schemas import PushOut, PushScanOut, PushTriggerIn
from app.modules.push.service import (
    dispatch_push,
    list_pushes,
    scan_all_users,
)

router = APIRouter(prefix="/push", tags=["push"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[User, Depends(get_current_user)]


@router.get("", response_model=list[PushOut])
async def list_entries(session: SessionDep, current: UserDep) -> list[PushOut]:
    msgs = await list_pushes(session, current.id)
    return [PushOut(**m.model_dump()) for m in msgs]


@router.post("/trigger", response_model=PushOut, status_code=status.HTTP_201_CREATED)
async def trigger(
    payload: PushTriggerIn, session: SessionDep, current: UserDep
) -> PushOut:
    """手动触发一条推送（测试 / 调试）。受每日限流与护栏约束。"""
    msg = await dispatch_push(
        session, current.id, payload.event_type, payload.title, payload.body
    )
    return PushOut(**msg.model_dump())


@router.post("/scan", response_model=PushScanOut)
async def scan(session: SessionDep, current: UserDep) -> PushScanOut:
    """手动跑一次事件巡检（测试用）。"""
    scanned, fired = await scan_all_users(session)
    return PushScanOut(scanned=scanned, fired=fired)
