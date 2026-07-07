"""diet 模块路由（api 层）。只做参数校验、鉴权、调 service、返回。"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.modules.auth.api import get_current_user
from app.modules.auth.domain import User
from app.modules.diet.schemas import CorrectRequest, DietEntryOut, RecognizeResponse
from app.modules.diet.service import correct_diet, list_diet, recognize_diet

logger = logging.getLogger("fitness_agent.diet")

router = APIRouter(prefix="/diet", tags=["diet"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[User, Depends(get_current_user)]


@router.post("/recognize", response_model=RecognizeResponse, status_code=status.HTTP_201_CREATED)
async def recognize(
    session: SessionDep,
    current: UserDep,
    image: UploadFile = File(...),
) -> RecognizeResponse:
    try:
        return await recognize_diet(session, current.id, image)
    except Exception as exc:  # noqa: BLE001 - 统一兜底，避免内部细节泄漏
        logger.error("识别失败: type=%s, msg=%s", type(exc).__name__, exc, exc_info=True)
        detail = f"识别失败: {exc}" if str(exc) else f"识别失败: [{type(exc).__name__}]"
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)


@router.post("/{entry_id}/correct", response_model=DietEntryOut)
async def correct(
    entry_id: int,
    payload: CorrectRequest,
    session: SessionDep,
    current: UserDep,
) -> DietEntryOut:
    try:
        entry = await correct_diet(session, current.id, entry_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return DietEntryOut(**entry.model_dump())


@router.get("", response_model=list[DietEntryOut])
async def list_entries(session: SessionDep, current: UserDep) -> list[DietEntryOut]:
    entries = await list_diet(session, current.id)
    return [DietEntryOut(**e.model_dump()) for e in entries]
