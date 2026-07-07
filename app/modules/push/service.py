"""push 模块业务逻辑（service 层）。

推送闭环（设计稿《策划书》§五 / 技术架构）：
  - 每日固定 2 条：晨间提醒(morning_nudge) + 晚间回顾(evening_recap)
  - 每日事件 1 条：事件巡检触发（一期实现「放弃预警」：连续 2 天无记录）
  - 护栏：每日 ≤3 条限流；建议类推送强制非医疗诊断免责；伤病信号时拦截"加量/加练"
  - 文案数据感知：复用 build_context 汇总近况，无真 key 用模板降级（确定、不烧钱）

dispatch_push 是统一出口：先过限流 → 再过内容护栏 → 落库（含 blocked 记录便于审计）。
业务层不直连调度器，scheduler.py 只调本文件的函数。
"""
from datetime import datetime, timedelta, timezone

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent.context import build_context
from app.agent.guardrails import push_content_safe
from app.core.redis import rate_incr  # 推送 ≤3/天 限流计数（Redis 缺失自动回退 SQL）
from app.modules.push.domain import PushMessage

# 每日每用户推送上限（设计稿：每日 ≤3 条）
DAILY_PUSH_LIMIT = 3

# 需附非医疗诊断免责的事件类型（含"建议"性质）
ADVICE_EVENT_TYPES = {"evening_recap", "abandon_warning"}

DISCLAIMER = "（以上为基于你记录的通用建议，非医疗诊断。）"

# 放弃预警判定：连续 N 天无任何记录
ABANDON_GAP_DAYS = 2

# 默认模板文案
TEMPLATES = {
    "morning_nudge": ("早安，新的一天开始啦", "记得记录今天的饮食和训练 💪 哪怕只记一笔，AI 教练也能更懂你。"),
    "evening_recap": ("今日回顾", "今天辛苦了，来看看你的记录小结～"),
    "abandon_warning": ("好久不见～", "看到你最近两天没记录啦，不着急。哪怕记一口饭、一次散步也是进步，需要时我都在。"),
}


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _ensure_disclaimer(body: str, event_type: str) -> str:
    """合规护栏：建议类推送末尾必须带非医疗诊断免责。"""
    if event_type in ADVICE_EVENT_TYPES and DISCLAIMER not in (body or ""):
        return f"{body}\n{DISCLAIMER}"
    return body


async def count_today_pushes(session: AsyncSession, user_id: int) -> int:
    """当日已发出（含被拦截）的推送条数。"""
    today = _today_str()
    since = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    result = await session.execute(
        select(func.count(PushMessage.id)).where(
            PushMessage.user_id == user_id, PushMessage.created_at >= since
        )
    )
    return int(result.scalar() or 0)


async def _already_sent_today(session: AsyncSession, user_id: int, event_type: str) -> bool:
    """防止定时任务重复触发：同一事件类型当天已发过则跳过。"""
    today = _today_str()
    since = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    result = await session.execute(
        select(func.count(PushMessage.id)).where(
            PushMessage.user_id == user_id,
            PushMessage.event_type == event_type,
            PushMessage.created_at >= since,
        )
    )
    return int(result.scalar() or 0) > 0


async def _today_push_count(session: AsyncSession, user_id: int) -> int:
    """当日已发（含被拦截）推送数。

    优先用 Redis 计数（INCR，TTL 到当天结束）；Redis 不可用时回退 SQL 表计数。
    返回的是"本次调用前已记录"的数量（Redis 路径已把本次 +1，故减回）。
    """
    redis_n = await rate_incr(f"push:count:{user_id}:{_today_str()}", ttl=86400)
    if redis_n is not None:
        return redis_n - 1
    return await count_today_pushes(session, user_id)


async def has_injury_signal(session: AsyncSession, user_id: int) -> bool:
    """伤病信号：近 14 天训练备注出现 疼/伤/痛（护栏依据，非诊断）。"""
    since = datetime.now(timezone.utc) - timedelta(days=14)
    from app.modules.training.domain import TrainingEntry

    t = await session.execute(
        select(TrainingEntry).where(
            TrainingEntry.user_id == user_id, TrainingEntry.created_at >= since
        )
    )
    notes = " ".join((e.notes or "") for e in t.scalars().all())
    return any(k in notes for k in ("疼", "伤", "痛"))


async def dispatch_push(
    session: AsyncSession,
    user_id: int,
    event_type: str,
    title: str | None = None,
    body: str | None = None,
) -> PushMessage:
    """统一出口：限流 → 内容护栏 → 落库。返回 PushMessage（status=sent/blocked）。"""
    # 1) 限流（Redis 计数优先，缺失时回退 SQL）
    sent = await _today_push_count(session, user_id)
    if sent >= DAILY_PUSH_LIMIT:
        msg = PushMessage(
            user_id=user_id,
            event_type=event_type,
            title=title or TEMPLATES.get(event_type, ("推送", ""))[0],
            body=body or "",
            status="blocked",
            blocked_reason=f"已达每日推送上限（{DAILY_PUSH_LIMIT} 条），本日不再打扰",
        )
        session.add(msg)
        await session.commit()
        await session.refresh(msg)
        return msg

    # 2) 默认模板 + 数据感知文案
    if title is None or body is None:
        t_title, t_body = TEMPLATES.get(event_type, (event_type, ""))
        title = title or t_title
        if body is None:
            body = await _compose_body(session, user_id, event_type, t_body)

    # 3) 内容护栏（伤病信号拦截危险鼓励）
    injury = await has_injury_signal(session, user_id)
    verdict = push_content_safe(body, injury)
    if not verdict.allowed:
        msg = PushMessage(
            user_id=user_id,
            event_type=event_type,
            title=title,
            body=body,
            status="blocked",
            blocked_reason="；".join(verdict.reasons),
        )
        session.add(msg)
        await session.commit()
        await session.refresh(msg)
        return msg

    # 4) 合规免责
    body = _ensure_disclaimer(body, event_type)

    msg = PushMessage(
        user_id=user_id,
        event_type=event_type,
        title=title,
        body=body,
        status="sent",
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


async def _compose_body(session: AsyncSession, user_id: int, event_type: str, base: str) -> str:
    """数据感知文案：用 build_context 汇总近况，失败降级为模板。"""
    if event_type == "evening_recap":
        try:
            ctx = await build_context(session, user_id, days=1)
            return f"{base}\n\n{ctx}"
        except Exception:
            return base
    return base


async def send_morning_nudge(session: AsyncSession, user_id: int) -> PushMessage | None:
    if await _already_sent_today(session, user_id, "morning_nudge"):
        return None
    return await dispatch_push(session, user_id, "morning_nudge")


async def send_evening_recap(session: AsyncSession, user_id: int) -> PushMessage | None:
    if await _already_sent_today(session, user_id, "evening_recap"):
        return None
    return await dispatch_push(session, user_id, "evening_recap")


async def _last_activity_day(session: AsyncSession, user_id: int) -> str | None:
    """最近一次记录的日期（饮食 created_at 或 训练 date 的较大者）。"""
    from app.modules.diet.domain import DietEntry
    from app.modules.training.domain import TrainingEntry

    d = await session.execute(
        select(func.max(DietEntry.created_at)).where(DietEntry.user_id == user_id)
    )
    t = await session.execute(
        select(func.max(TrainingEntry.date)).where(TrainingEntry.user_id == user_id)
    )
    d_day = d.scalar()
    t_day = t.scalar()
    candidates: list[str] = []
    if d_day:
        candidates.append(d_day.strftime("%Y-%m-%d"))
    if t_day:
        candidates.append(str(t_day))
    return max(candidates) if candidates else None


async def scan_events_for_user(session: AsyncSession, user_id: int) -> bool:
    """事件巡检（一期：放弃预警）。命中且当日未发过则推送，返回是否触发。"""
    last = await _last_activity_day(session, user_id)
    today = _today_str()
    gap_ok = (last is None) or (
        datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")
    ).days >= ABANDON_GAP_DAYS
    if not gap_ok:
        return False
    if await _already_sent_today(session, user_id, "abandon_warning"):
        return False
    await dispatch_push(session, user_id, "abandon_warning")
    return True


async def scan_all_users(session: AsyncSession) -> tuple[int, int]:
    """遍历全量用户跑事件巡检。返回 (扫描数, 触发数)。"""
    from app.modules.auth.domain import User

    users = (await session.execute(select(User))).scalars().all()
    fired = 0
    for u in users:
        if await scan_events_for_user(session, u.id):
            fired += 1
    await session.commit()
    return len(users), fired


async def list_pushes(session: AsyncSession, user_id: int, limit: int = 50) -> list[PushMessage]:
    result = await session.execute(
        select(PushMessage)
        .where(PushMessage.user_id == user_id)
        .order_by(PushMessage.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
