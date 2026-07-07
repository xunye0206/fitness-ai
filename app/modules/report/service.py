"""report 模块业务逻辑（service 层）。

报告生成闭环：
  召回 7 天上下文(build_context) → 调 router.reason 生成 → 解析结构化结果
  → 护栏（合规免责、降级兜底）→ 落库 DailyReport。

业务层只通过 app.llm.router.reason 调 LLM；无 key 时用 fake provider 也能确定性跑通。
"""
import json
import re
from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent.context import build_context
from app.config import settings
from app.llm.base import Message
from app.llm.router import reason
from app.modules.memory.service import index_wiki  # 把报告写入向量库（跨周期记忆）
from app.modules.report.domain import DailyReport

# 固定系统前缀（Prefix Cache 友好，争取缓存命中）
SYSTEM_PROMPT = (
    "你是用户的健身陪伴助手（非医疗诊断）。"
    "基于用户近况数据，给出当日小结与可执行建议。"
    "建议必须基于数据、具体、可操作；不得出现诊断/治疗/体脂率医疗化表述。"
    "严格按要求输出 JSON：{\"summary\": \"...\", \"advice\": \"...\"}。"
)

DISCLAIMER = "（以上为基于你记录的通用建议，非医疗诊断。）"


def _extract_json(text: str) -> dict | None:
    """从模型返回里抠出 JSON；兼容 ```json 围栏与正文夹杂。失败返回 None（走降级）。"""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    try:
        obj = json.loads(candidate)
    except Exception:
        # 退一步：尝试在整个文本里找第一个 { 到最后一个 } 的子串
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                obj = json.loads(candidate[start : end + 1])
            except Exception:
                return None
        else:
            return None
    if isinstance(obj, dict) and "summary" in obj:
        return obj
    return None


def _ensure_disclaimer(advice: str) -> str:
    """合规护栏：建议末尾必须带非医疗诊断免责。"""
    return advice if DISCLAIMER in advice else f"{advice}\n{DISCLAIMER}"


async def generate_report(
    session: AsyncSession,
    user_id: int,
    report_date: str | None = None,
    days: int = 7,
) -> DailyReport:
    report_date = report_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    context = await build_context(session, user_id, days)

    user_prompt = (
        f"以下是用户近 {days} 天的记录回顾：\n{context}\n\n"
        "请生成今日小结(summary)与建议(advice)，输出 JSON。"
    )
    result = await reason([Message(role="system", content=SYSTEM_PROMPT),
                           Message(role="user", content=user_prompt)])

    parsed = _extract_json(result.text) if result.ok else None
    if parsed is None:
        # 降级：模型失败或无法解析 → 用上下文本身作为小结，附通用建议，不中断流程
        summary = f"近 {days} 天记录已汇总（模型暂未生成结构化小结）。"
        advice = "建议：保持每天记录饮食与训练，数据越完整，建议越精准。"
    else:
        summary = str(parsed.get("summary", "")).strip() or "（无小结）"
        advice = str(parsed.get("advice", "")).strip() or "（无建议）"

    advice = _ensure_disclaimer(advice)

    report = DailyReport(
        user_id=user_id,
        report_date=report_date,
        summary=summary,
        advice=advice,
        raw_context=context,
        model=settings.reasoning_provider,
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)

    # 把报告摘要写入向量库，作为跨周期的长期记忆（下次生成报告时 recall 召回）。
    # 失败优雅降级（embedding 不可用/落库异常），绝不影响报告主链路。
    try:
        await index_wiki(
            session, user_id,
            source=f"report:{report_date}",
            text=f"{summary}\n{advice}",
        )
    except Exception:
        pass

    return report


async def list_reports(
    session: AsyncSession, user_id: int, limit: int = 30
) -> list[DailyReport]:
    result = await session.execute(
        select(DailyReport)
        .where(DailyReport.user_id == user_id)
        .order_by(DailyReport.report_date.desc(), DailyReport.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
