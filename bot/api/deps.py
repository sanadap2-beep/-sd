"""FastAPI dependencies for database and authenticated users."""

from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException
from sqlalchemy import select

from api.auth import current_telegram_user
from database.engine import async_session_maker
from database.models import User


async def get_session() -> AsyncGenerator:
    async with async_session_maker() as session:
        yield session


async def get_current_user(
    tg_user: dict = Depends(current_telegram_user),
    session=Depends(get_session),
) -> User:
    result = await session.execute(select(User).where(User.telegram_id == int(tg_user["id"])))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            telegram_id=int(tg_user["id"]),
            username=tg_user.get("username"),
            full_name=tg_user.get("first_name", ""),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def get_current_admin(
    user: User = Depends(get_current_user),
) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin access required")
    return user
