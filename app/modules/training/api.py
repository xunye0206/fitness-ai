"""training 模块路由（api 层）。只做参数校验、鉴权、调 service、返回。"""
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.modules.auth.api import get_current_user
from app.modules.auth.domain import User
from app.modules.training.schemas import TrainingCreate, TrainingOut
from app.modules.training.service import list_training, record_training

router = APIRouter(prefix="/training", tags=["training"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[User, Depends(get_current_user)]


@router.post("", response_model=TrainingOut, status_code=status.HTTP_201_CREATED)
async def create_entry(
    payload: TrainingCreate, session: SessionDep, current: UserDep
) -> TrainingOut:
    entry = await record_training(session, current.id, payload)
    return TrainingOut(**entry.model_dump())


@router.get("", response_model=list[TrainingOut])
async def list_entries(session: SessionDep, current: UserDep) -> list[TrainingOut]:
    entries = await list_training(session, current.id)
    return [TrainingOut(**e.model_dump()) for e in entries]
