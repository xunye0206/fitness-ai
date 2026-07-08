"""M10 长期画像记忆：Agent 自动维护用户叙事记忆（画像 / 反思 / 洞察）。

设计要点（对回代码规范 §121「向量记忆 → memory_embeddings」+ §6「输出须个性化」）：
- 在聊天回合结束后，从「用户本轮发言 + 教练回复」抽取三类画像记忆：
    · 画像：稳定的用户特征（目标、偏好、禁忌、作息、伤病史）
    · 反思：行为规律 / 因果（"一熬夜第二天必练崩""讨厌西兰花"）
    · 洞察：基于对话可累计的结论（"最近总在宵夜后超量""下肢偏弱"）
- 三类分别 index_wiki 写入 memory_embeddings，语义召回时三类都在用户记忆里。
- 聊天时再用「用户当前发言」定向 recall_profile，把画像注入教练上下文，实现个性化闭环。
- 调用 LLM / embedding 失败一律降级跳过，绝不拖垮聊天 / 报告主链路。
- 抽取、写入、召回核心函数均可注入 reason_fn / embed_fn，便于测试用 fake 不联网验证。

合规边界：画像属「话术级记忆」，写 memory_embeddings（数据库）对回红线
「数值只进数据库，绝不进 wiki」；绝不写体重 / 热量等数值进画像文本。
"""
import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.llm.base import LLMResult, Message
from app.core.db import async_session_factory
from app.modules.memory.service import index_wiki, recall, RecallHit

logger = logging.getLogger("fitness_agent.profile")

# 三类画像记忆的中文键 → memory_embeddings.source 标识
PROFILE_SOURCES: dict[str, str] = {
    "画像": "profile/画像",
    "反思": "profile/反思",
    "洞察": "profile/洞察",
}

PROFILE_EXTRACT_PROMPT = (
    "你负责从一段健身教练与用户的对话中，抽取可长期记住的用户画像记忆。\n"
    "只抽取「稳定且对未来建议有用」的信息，不要复述具体某次饮食/训练的流水账数值。\n"
    "分三类：\n"
    "- 画像：用户的目标、偏好、禁忌、作息、伤病史等稳定特征\n"
    "- 反思：用户的行为规律或因果（如『一熬夜第二天必练崩』『讨厌西兰花』）\n"
    "- 洞察：基于对话可累计的结论（如『最近总在宵夜后超量』『下肢力量偏弱』）\n"
    "若某类无新信息，对应字段给空字符串。\n"
    '只返回一个 JSON 对象，不要任何额外文字，格式：{"画像":"","反思":"","洞察":""}'
)


def _extract_json(text: str):
    """从模型文本里稳健抽取第一个 JSON 对象。"""
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return None
    return None


async def extract_profile_memory(
    user_msg: str,
    ai_reply: str,
    reason_fn: Callable[[list[Message]], Awaitable[LLMResult]] | None = None,
) -> dict[str, str]:
    """从一轮对话抽取三类画像记忆，返回 {中文类别名: 文本}。空类别不入。

    总开关关闭或 LLM 失败时返回 {}（不写库）。
    """
    if not get_settings().profile_memory_enabled:
        return {}
    from app.llm import router  # 延迟导入，避免循环依赖

    reason = reason_fn or router.reason
    try:
        result = await reason([
            Message(role="system", content=PROFILE_EXTRACT_PROMPT),
            Message(role="user", content=f"用户说：{user_msg}\n\n教练回：{ai_reply}"),
        ])
    except Exception as exc:
        logger.warning("画像抽取 LLM 调用失败，跳过：%s", exc)
        return {}
    if not result.ok or not result.text:
        return {}
    obj = _extract_json(result.text)
    if not isinstance(obj, dict):
        return {}
    out: dict[str, str] = {}
    for key in PROFILE_SOURCES:
        val = (obj.get(key) or "").strip()
        if val:
            out[key] = val
    return out


async def store_profile_memory(
    session: AsyncSession,
    user_id: int,
    profiles: dict[str, str],
    embed_fn: Callable[[list[str]], Awaitable[LLMResult]] | None = None,
) -> None:
    """把抽取到的画像写入 memory_embeddings（三类分别 index，幂等覆盖旧块）。"""
    for key, text in profiles.items():
        source = PROFILE_SOURCES.get(key, f"profile/{key}")
        try:
            await index_wiki(session, user_id, source=source, text=text, embed=embed_fn)
        except Exception as exc:
            logger.warning("画像写入失败 source=%s：%s", source, exc)


async def update_profile_memory(
    user_id: int,
    user_msg: str,
    ai_reply: str,
    session: Optional[AsyncSession] = None,
    reason_fn: Callable[[list[Message]], Awaitable[LLMResult]] | None = None,
    embed_fn: Callable[[list[str]], Awaitable[LLMResult]] | None = None,
) -> None:
    """端到端：抽取 + 写入用户长期画像。

    - session 未传时自己开独立 session（fire-and-forget 安全，不依赖 request 生命周期）。
    - 整体 try/except 包裹，任何异常降级跳过，绝不抛给调用方。
    """
    if not get_settings().profile_memory_enabled:
        return
    own_session = session is None
    sess: AsyncSession = session if session is not None else async_session_factory()
    try:
        profiles = await extract_profile_memory(user_msg, ai_reply, reason_fn=reason_fn)
        if profiles:
            await store_profile_memory(sess, user_id, profiles, embed_fn=embed_fn)
    except Exception as exc:
        logger.warning("画像记忆更新失败 user=%d：%s", user_id, exc)
    finally:
        if own_session:
            try:
                await sess.close()
            except Exception:
                pass


async def recall_profile(
    session: AsyncSession,
    user_id: int,
    query: str,
    k: int = 3,
    embed_fn: Callable[[list[str]], Awaitable[LLMResult]] | None = None,
) -> list[RecallHit]:
    """聊天时定向召回用户长期画像：用「用户当前发言」作 query 比聚合文本更精准。

    总开关关闭或 embedding 不可用时返回 []（降级），调用方据此跳过注入。
    """
    if not get_settings().profile_memory_enabled:
        return []
    try:
        return await recall(session, user_id, query, k=k, embed=embed_fn)
    except Exception as exc:
        logger.warning("画像召回失败 user=%d：%s", user_id, exc)
        return []


async def _safe_background_update(user_id: int, user_msg: str, ai_reply: str) -> None:
    """fire-and-forget 包装：吞掉一切异常，避免后台任务崩溃影响事件循环。"""
    try:
        await update_profile_memory(user_id, user_msg, ai_reply)
    except Exception as exc:  # pragma: no cover - 防御性兜底
        logger.warning("画像记忆后台更新异常 user=%d：%s", user_id, exc)


def schedule_profile_update(user_id: int, user_msg: str, ai_reply: str) -> None:
    """在聊天流式返回后触发画像记忆后台更新（不阻塞 SSE 流）。"""
    try:
        asyncio.create_task(_safe_background_update(user_id, user_msg, ai_reply))
    except Exception as exc:  # pragma: no cover - 极端情况下（无 running loop）跳过
        logger.warning("画像记忆后台调度失败 user=%d：%s", user_id, exc)
