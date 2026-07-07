"""auth 模块业务逻辑（service 层）。"""
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.modules.auth.domain import User


async def register(session: AsyncSession, username: str, password: str) -> User:
    result = await session.execute(select(User).where(User.username == username))
    if result.scalars().first() is not None:
        raise ValueError("用户名已存在")
    user = User(username=username, password_hash=hash_password(password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate(session: AsyncSession, username: str, password: str) -> User | None:
    result = await session.execute(select(User).where(User.username == username))
    user = result.scalars().first()
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def issue_token(user: User) -> str:
    return create_access_token(user.id)
