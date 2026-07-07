"""agent 横切层暴露的对外接口：AI 教练对话。

区别于功能模块（diet/training/...），对话是 agent 的核心交互面——
用户直接对教练说话，教练基于「近期数据 + 语义记忆 + 护栏」作答。
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.llm.base import Message
from app.llm.router import reason
from app.agent.context import build_context
from app.modules.auth.api import get_current_user
from app.modules.auth.domain import User

router = APIRouter(prefix="/agent", tags=["agent"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[User, Depends(get_current_user)]

# 教练人设与硬边界（对应项目合规底线：只给建议、不做医疗诊断）
COACH_SYSTEM_PROMPT = """你是「健身AI」的专属 AI 健身教练，是用户的中文私人教练。

职责：基于用户的饮食 / 训练 / 报告数据，给出个性化、可执行、鼓励性的健身与营养建议。

严格边界（务必遵守）：
1. 你不是医生，不做任何医疗诊断、不开药、不评价病情。涉及伤痛时只建议休息并提示必要时就医。
2. 不编造用户没有记录过的数据；一切以下方「用户近期数据」为准。
3. 建议具体、简短、可落地，用中文口语化表达，像真人教练一样有温度、正向鼓励。
4. 涉及热量 / 体重等敏感话题时保持正向，不制造身材焦虑。
5. 如果用户只是闲聊或问非健身话题，友好回应并温和引导回健康话题。
"""


class ChatIn(BaseModel):
    message: str
    history: list[dict] = []  # 前端维护的对话历史：[{"role":"user|assistant","content":"..."}]


class ChatOut(BaseModel):
    reply: str
    ok: bool = True


@router.post("/chat", response_model=ChatOut)
async def chat(payload: ChatIn, session: SessionDep, current: UserDep) -> ChatOut:
    """AI 教练对话。组装用户近期数据 + 语义记忆作为上下文，调推理模型作答。

    失败（模型/网络）时优雅降级为友好提示，不抛 500。
    """
    # 1) 组装上下文（近 7 天 + 语义记忆；embedding 未启用时自动降级）
    try:
        ctx = await build_context(session, current.id, days=7, use_semantic=True)
    except Exception:
        ctx = "（暂无可用的近期数据）"

    system = COACH_SYSTEM_PROMPT + "\n\n【用户近期数据】\n" + (ctx or "（暂无记录）")

    messages: list[Message] = [Message(role="system", content=system)]
    # 2) 带入最近若干轮历史（避免上下文过长）
    for h in (payload.history or [])[-6:]:
        role = h.get("role")
        content = h.get("content")
        if role in ("user", "assistant") and content:
            messages.append(Message(role=role, content=content))
    messages.append(Message(role="user", content=payload.message.strip()))

    # 3) 推理
    res = await reason(messages)
    if not res.ok:
        return ChatOut(
            reply="抱歉，教练这会儿有点忙，稍后再来聊吧～（若持续出现可检查模型配置）",
            ok=False,
        )
    return ChatOut(reply=res.text or "（教练没有返回内容）", ok=True)
