"""training 模块业务逻辑（service 层）。

手动录入训练记录，纯 SQLite 落库，不依赖 LLM。
跨模块召回（日报用）通过 app.agent.context 走 SQL 聚合，不在此处提供。
"""
import base64

from datetime import datetime, timezone

from fastapi import UploadFile
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent import run_training_recognition
from app.agent.schemas import TrainingEstimate
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
        distance_km=payload.distance_km,
        sets=payload.sets,
        reps=payload.reps,
        pace=payload.pace,
        avg_hr=payload.avg_hr,
        source=payload.source,
        notes=payload.notes,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def recognize_training(session: AsyncSession, user_id: int, image: UploadFile) -> dict:
    """训练截图识别：解析结构化训练数据，返回给前端确认（不直接落库，避免错数据入库）。

    视觉失败或解析失败时 estimate 为 None，由前端引导手动录入。
    """
    data = await image.read()
    image_b64 = base64.b64encode(data).decode("utf-8")

    result = await run_training_recognition(user_id, image_b64)
    estimate: TrainingEstimate | None = result.get("recognition")
    verdict = result.get("verdict")

    if estimate is None:
        return {
            "estimate": None,
            "needs_confirmation": True,
            "guardrail_reasons": ["视觉识别失败，请手动录入"],
        }

    needs_confirmation = bool(verdict and verdict.needs_confirmation)
    return {
        "estimate": estimate.model_dump(),
        "needs_confirmation": needs_confirmation,
        "guardrail_reasons": verdict.reasons if verdict else [],
    }


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
