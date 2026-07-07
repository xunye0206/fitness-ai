"""auth 模块路由（api 层）。只做参数校验、鉴权、调 service、返回。"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.core.security import decode_access_token
from app.modules.auth.domain import User
from app.modules.auth.service import authenticate, issue_token, register

router = APIRouter(prefix="/auth", tags=["auth"])

bearer = HTTPBearer(auto_error=False)
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserPublic(BaseModel):
    id: int
    username: str
    created_at: datetime


@router.post(
    "/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
async def register_user(payload: RegisterRequest, session: SessionDep) -> TokenResponse:
    try:
        user = await register(session, payload.username, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return TokenResponse(access_token=issue_token(user))


@router.post("/login", response_model=TokenResponse)
async def login_user(payload: LoginRequest, session: SessionDep) -> TokenResponse:
    user = await authenticate(session, payload.username, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    return TokenResponse(access_token=issue_token(user))


async def get_current_user(
    session: SessionDep,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> User:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供令牌")
    user_id = decode_access_token(creds.credentials)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user


@router.get("/me", response_model=UserPublic)
async def me(current: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic(id=current.id, username=current.username, created_at=current.created_at)
