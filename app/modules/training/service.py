"""training 模块业务逻辑（service 层）。

手动录入训练记录，纯 SQLite 落库，不依赖 LLM。
跨模块召回（日报用）通过 app.agent.context 走 SQL 聚合，不在此处提供。
"""
from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.training.domain import TrainingEntry
from app.modules.training.schemas import TrainingCreate


async def record_training(
    session: AsyncSession, user_id: int, payload: TrainingCreate
) -> TrainingEntry:
    entry = TrainingEntry(
        user_id=user_id,
        date=payload.date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        exercise_type=payload.exercise_type,
        duration_min=payload.duration_min,
        intensity=payload.intensity,
        calories_burned=payload.calories_burned,
        notes=payload.notes,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def list_training(
    session: AsyncSession, user_id: int, limit: int = 50
) -> list[TrainingEntry]:
    result = await session.execute(
        select(TrainingEntry)
        .where(TrainingEntry.user_id == user_id)
        .order_by(TrainingEntry.date.desc(), TrainingEntry.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
