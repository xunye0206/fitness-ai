"""report 模块路由（api 层）。只做参数校验、鉴权、调 service、返回。"""
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.modules.auth.api import get_current_user
from app.modules.auth.domain import User
from app.modules.report.schemas import ReportGenerateRequest, ReportOut
from app.modules.report.service import generate_report, list_reports

router = APIRouter(prefix="/report", tags=["report"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[User, Depends(get_current_user)]


@router.post("/generate", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
async def generate(
    payload: ReportGenerateRequest, session: SessionDep, current: UserDep
) -> ReportOut:
    report = await generate_report(session, current.id, payload.report_date, payload.days)
    return ReportOut(**report.model_dump())


@router.get("", response_model=list[ReportOut])
async def list_entries(session: SessionDep, current: UserDep) -> list[ReportOut]:
    reports = await list_reports(session, current.id)
    return [ReportOut(**r.model_dump()) for r in reports]
