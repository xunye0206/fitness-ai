"""diet 模块业务逻辑（service 层）。

核心链路：保存图片 → 调 Agent 状态机识图 → 落库（护栏决定 pending/confirmed）。
业务层不直接调用 LLM，只通过 app.agent.run_diet_recognition 编排。
"""
import base64
import os
import uuid

from datetime import datetime
from fastapi import UploadFile
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent import run_diet_recognition
from app.agent.context import invalidate_context_cache
from app.agent.schemas import FoodEstimate
from app.config import settings
from app.modules.diet.domain import CST, DietEntry

# 餐次区间（按北京时间小时判断，无需用户手动选择）
_MEAL_RULES = [
    (5, 11, "breakfast"),       # 05:00–10:59 早餐
    (11, 15, "lunch"),          # 11:00–14:59 午餐
    (15, 18, "afternoon_tea"),  # 15:00–17:59 下午茶
    (18, 22, "dinner"),         # 18:00–21:59 晚餐
]


def infer_meal_type(dt: datetime | None) -> str:
    """根据记录时间（转北京时间）推断餐次，返回英文枚举。"""
    if dt is None:
        return "other"
    hour = dt.astimezone(CST).hour if dt.tzinfo is not None else dt.hour
    for start, end, label in _MEAL_RULES:
        if start <= hour < end:
            return label
    return "midnight_snack"  # 22:00–04:59 宵夜


def _save_image(data: bytes, filename: str) -> str:
    os.makedirs(settings.upload_dir, exist_ok=True)
    ext = os.path.splitext(filename)[1] or ".png"
    path = os.path.join(settings.upload_dir, f"{uuid.uuid4().hex}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    return path


async def recognize_diet(session: AsyncSession, user_id: int, image: UploadFile) -> dict:
    data = await image.read()
    image_path = _save_image(data, image.filename or "upload.png")
    image_b64 = base64.b64encode(data).decode("utf-8")

    result = await run_diet_recognition(user_id, image_b64)
    estimate: FoodEstimate | None = result.get("recognition")
    verdict = result.get("verdict")

    # 视觉失败：仍落一条 pending 记录，让用户手动录入
    if estimate is None:
        entry = DietEntry(user_id=user_id, image_path=image_path, name="", status="pending",
                          raw_estimate="视觉识别失败", meal_type=infer_meal_type(datetime.now(CST)))
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        await invalidate_context_cache(user_id)  # 写入后使教练上下文缓存失效
        return {
            "entry_id": entry.id,
            "estimate": None,
            "needs_confirmation": True,
            "guardrail_reasons": ["视觉识别失败，请手动录入"],
        }

    needs_confirmation = bool(verdict and verdict.needs_confirmation)
    entry = DietEntry(
        user_id=user_id,
        image_path=image_path,
        name=estimate.name,
        calories=estimate.calories,
        protein_g=estimate.protein_g,
        carbs_g=estimate.carbs_g,
        fat_g=estimate.fat_g,
        confidence=estimate.confidence,
        status="confirmed" if not needs_confirmation else "pending",
        raw_estimate=estimate.note,
    )
    entry.meal_type = infer_meal_type(entry.created_at)
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    await invalidate_context_cache(user_id)  # 写入后使教练上下文缓存失效
    return {
        "entry_id": entry.id,
        "estimate": estimate,
        "needs_confirmation": needs_confirmation,
        "guardrail_reasons": verdict.reasons if verdict else [],
    }


async def correct_diet(
    session: AsyncSession, user_id: int, entry_id: int, correction: "CorrectRequest"
) -> DietEntry:
    entry = await session.get(DietEntry, entry_id)
    if entry is None or entry.user_id != user_id:
        raise ValueError("记录不存在或无权限")

    if correction.name is not None:
        entry.name = correction.name
    if correction.calories is not None:
        entry.calories = correction.calories
    if correction.protein_g is not None:
        entry.protein_g = correction.protein_g
    if correction.carbs_g is not None:
        entry.carbs_g = correction.carbs_g
    if correction.fat_g is not None:
        entry.fat_g = correction.fat_g
    entry.status = "confirmed"

    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    await invalidate_context_cache(user_id)  # 修正后同样失效上下文缓存
    return entry


async def list_diet(session: AsyncSession, user_id: int, limit: int = 50) -> list[DietEntry]:
    result = await session.execute(
        select(DietEntry)
        .where(DietEntry.user_id == user_id)
        .order_by(DietEntry.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
