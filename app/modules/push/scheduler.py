"""推送调度（APScheduler）：每日固定 2 条 + 事件巡检 1 条。

设计稿约定：APScheduler 定时巡检 + 事件触发，每日 ≤3 条。
- 08:00 晨间提醒(morning_nudge)
- 21:00 晚间回顾(evening_recap)
- 12:00 事件巡检(scan_events_for_user)：一期实现「放弃预警」

调度器在 FastAPI lifespan 里启停；job 内部用 async_session_factory 自建会话，
不依赖请求上下文。限流/护栏在 service.dispatch_push 统一处理。
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.db import async_session_factory
from app.core.logging import logger
from app.modules.push import service as push_svc

# 中国时区，符合用户作息
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def _run_fixed(kind: str) -> None:
    from app.modules.auth.domain import User
    from sqlmodel import select

    async with async_session_factory() as session:
        users = (await session.execute(select(User))).scalars().all()
        for u in users:
            if kind == "morning":
                await push_svc.send_morning_nudge(session, u.id)
            else:
                await push_svc.send_evening_recap(session, u.id)
        await session.commit()


async def _run_event_scan() -> None:
    from app.modules.auth.domain import User
    from sqlmodel import select

    async with async_session_factory() as session:
        users = (await session.execute(select(User))).scalars().all()
        for u in users:
            await push_svc.scan_events_for_user(session, u.id)
        await session.commit()


def register_scheduler() -> None:
    """启动调度器。防御性包裹：即使调度器在当前环境无法启动，也不应拖垮整个 app。"""
    try:
        scheduler.add_job(
            _run_fixed, CronTrigger(hour=8, minute=0), args=["morning"],
            id="push_morning", replace_existing=True,
        )
        scheduler.add_job(
            _run_fixed, CronTrigger(hour=21, minute=0), args=["evening"],
            id="push_evening", replace_existing=True,
        )
        scheduler.add_job(
            _run_event_scan, CronTrigger(hour=12, minute=0),
            id="push_event_scan", replace_existing=True,
        )
        scheduler.start()
        logger.info("推送调度已启动（每日 08:00 / 21:00 固定 + 12:00 事件巡检）")
    except Exception as exc:  # pragma: no cover - 调度器为副作用，不可阻断主流程
        logger.warning("推送调度启动失败（不影响主接口）：%s", exc)


def shutdown_scheduler() -> None:
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception as exc:  # pragma: no cover
        logger.warning("推送调度关闭异常：%s", exc)
