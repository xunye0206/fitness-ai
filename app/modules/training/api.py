"""training 模块路由（api 层）。只做参数校验、鉴权、调 service、返回。"""
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.modules.auth.api import get_current_user
from app.modules.auth.domain import User
from app.modules.training.schemas import TrainingCreate, TrainingOut
from app.modules.training.service import list_training, record_training, recognize_training

router = APIRouter(prefix="/training", tags=["training"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[User, Depends(get_current_user)]


@router.post("", response_model=TrainingOut, status_code=status.HTTP_201_CREATED)
async def create_entry(
    payload: TrainingCreate, session: SessionDep, current: UserDep
) -> TrainingOut:
    entry = await record_training(session, current.id, payload)
    return TrainingOut(**entry.model_dump())


@router.post("/recognize")
async def recognize_entry(
    image: Annotated[UploadFile, File(...)], session: SessionDep, current: UserDep
) -> dict:
    """上传训练截图（Keep/悦跑圈等），视觉解析为结构化训练数据，返回供前端确认。

    不直接落库：前端展示可编辑结果，用户确认/修正后再 POST /training 保存。
    """
    return await recognize_training(session, current.id, image)


@router.get("", response_model=list[TrainingOut])
async def list_entries(session: SessionDep, current: UserDep) -> list[TrainingOut]:
    entries = await list_training(session, current.id)
    return [TrainingOut(**e.model_dump()) for e in entries]
