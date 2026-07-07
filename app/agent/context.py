"""Agent 上下文工程：组装近 7 天召回窗口（设计稿《Agent工程要点》§1）。

build_context 把"长期记忆"（SQLite 全量结构化）按 7 天窗口聚合，
输出纯文本摘要，喂给报告/建议生成。原则：
- 只传聚合结果，不传原始逐行数据（上下文剪枝、省 token）。
- 窗口长度可配置（默认 7），对应"防模型变笨"的召回窗口。
- 纯 SQL 查询，不依赖外部服务；测试可用 fake provider 全内存跑。
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.diet.domain import DietEntry
from app.modules.memory.domain import MemoryEmbedding  # noqa: F401  确保表进入 metadata 被建出
from app.modules.memory.service import recall as recall_memory
from app.modules.training.domain import TrainingEntry

# 召回窗口天数（默认 7，对应设计稿"近 7 天"）
DEFAULT_RECALL_DAYS = 7


def _date_n_days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


async def build_context(
    session: AsyncSession,
    user_id: int,
    days: int = DEFAULT_RECALL_DAYS,
    use_semantic: bool = True,
) -> str:
    """召回近 days 天饮食+训练，聚合为文本；可选追加语义召回的长期记忆。

    use_semantic=True 时，用本窗口聚合文本作为查询，召回用户跨周/跨月的叙事记忆
    （wiki 偏好/洞察）。embedding 未启用或失败时自动跳过，不影响主链路。
    """
    since = _date_n_days_ago(days - 1)  # 含今天共 days 天
    since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    try:
        diet_rows = await session.execute(
            select(DietEntry).where(
                DietEntry.user_id == user_id, DietEntry.created_at >= since_dt
            )
        )
    except Exception:
        diet_rows = None

    # 饮食按日期聚合（用 created_at 归到日期，简单稳妥）
    diet_by_day: dict[str, list[DietEntry]] = defaultdict(list)
    if diet_rows is not None:
        for e in diet_rows.scalars().all():
            day = e.created_at.strftime("%Y-%m-%d")
            diet_by_day[day].append(e)

    try:
        train_rows = await session.execute(
            select(TrainingEntry).where(
                TrainingEntry.user_id == user_id, TrainingEntry.date >= since
            )
        )
        train_entries = list(train_rows.scalars().all())
    except Exception:
        train_entries = []

    # 聚合文本
    lines: list[str] = [f"近 {days} 天回顾（窗口起点 {since}）：", ""]

    # 饮食聚合
    total_cal = 0.0
    diet_days = sorted(diet_by_day.keys())
    if diet_days:
        for day in diet_days:
            day_entries = diet_by_day[day]
            day_cal = sum(e.calories for e in day_entries)
            total_cal += day_cal
            names = "、".join(e.name or "未命名" for e in day_entries)
            lines.append(f"- {day} 饮食：{len(day_entries)} 条，约 {day_cal:.0f} kcal（{names}）")
        avg_cal = total_cal / len(diet_days)
        lines.append(f"  饮食日均摄入 ≈ {avg_cal:.0f} kcal（记录 {len(diet_days)} 天）")
    else:
        lines.append("- 饮食：近窗口内暂无记录")

    lines.append("")

    # 训练聚合
    if train_entries:
        total_min = sum(e.duration_min for e in train_entries)
        total_burn = sum(e.calories_burned for e in train_entries)
        types = defaultdict(int)
        for e in train_entries:
            types[e.exercise_type or "其他"] += 1
        type_str = "、".join(f"{k}×{v}" for k, v in types.items())
        lines.append(f"- 训练：{len(train_entries)} 次，共 {total_min} 分钟，消耗 ≈ {total_burn:.0f} kcal")
        lines.append(f"  项目分布：{type_str}")
    else:
        lines.append("- 训练：近窗口内暂无记录")

    # M5：语义召回长期记忆（跨周/跨月），embedding 不可用时 recall 返回 []，自动跳过
    if use_semantic:
        try:
            hits = await recall_memory(session, user_id, "\n".join(lines), k=5)
        except Exception:
            hits = []
        if hits:
            lines.append("")
            lines.append("长期记忆（语义召回，仅作参考）：")
            for h in hits:
                lines.append(f"- {h.text}（相关度 {h.score:.2f}）")

    return "\n".join(lines)
