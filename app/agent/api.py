"""agent 横切层暴露的对外接口：AI 教练对话（支持联动其他模块）。

区别于功能模块（diet/training/...），对话是 agent 的核心交互面——
用户直接对教练说话，教练基于「近期数据 + 语义记忆 + 护栏」作答；
当用户意图是"记饮食/记训练/出报告"时，教练通过工具调用真正去执行这些动作。
"""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent.graph import run_diet_recognition
from app.agent.tools import TOOLS, execute_tool
from app.core.db import get_session
from app.llm.base import LLMResult, Message
from app.llm.router import reason, reason_with_tools
from app.agent.context import build_context
from app.modules.auth.api import get_current_user
from app.modules.auth.domain import User
from app.modules.diet.domain import DietEntry

router = APIRouter(prefix="/agent", tags=["agent"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[User, Depends(get_current_user)]

# 教练人设与硬边界（对应项目合规底线：只给建议、不做医疗诊断）
COACH_SYSTEM_PROMPT = """你是「健身AI」的专属 AI 健身教练，是用户的中文私人教练。

职责：基于用户的饮食 / 训练 / 报告数据，给出个性化、可执行、鼓励性的健身与营养建议。
你还能直接帮用户记录饮食、记录训练、生成报告（通过工具调用自动完成）。

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
    image_base64: Optional[str] = None  # 可选：用户发来的食物图片（base64，不含 data: 前缀）


class ChatOut(BaseModel):
    reply: str
    ok: bool = True
    actions: list = []  # 本次对话实际执行的业务动作摘要（如"已记录饮食：..."），供前端展示


async def _handle_image(
    payload: ChatIn, session: AsyncSession, user_id: int
) -> str:
    """处理用户发来的食物图片：走视觉管线建饮食记录，返回给教练点评的注记。"""
    try:
        rec = await run_diet_recognition(user_id, payload.image_base64)
    except Exception:
        return "（用户发来一张食物图片，但视觉识别调用失败）"
    est = rec.get("recognition")
    verdict = rec.get("verdict")
    if est is None:
        return "（用户发来一张食物图片，但视觉识别未成功，建议用户稍后手动补充）"
    needs_confirm = bool(verdict and verdict.needs_confirmation)
    entry = DietEntry(
        user_id=user_id,
        image_path=None,
        name=est.name,
        calories=est.calories,
        protein_g=est.protein_g,
        carbs_g=est.carbs_g,
        fat_g=est.fat_g,
        confidence=est.confidence,
        status="confirmed" if not needs_confirm else "pending",
        raw_estimate=est.note,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    note = (
        f"（用户发来一张食物图片，已自动记录为饮食：{est.name} "
        f"约 {est.calories:.0f} kcal，蛋白 {est.protein_g:.0f}g / "
        f"碳水 {est.carbs_g:.0f}g / 脂肪 {est.fat_g:.0f}g）"
    )
    return note


@router.post("/chat", response_model=ChatOut)
async def chat(payload: ChatIn, session: SessionDep, current: UserDep) -> ChatOut:
    """AI 教练对话（含工具调用循环）。

    流程：组装上下文 → 调用带 tools 的推理 → 若模型发起工具调用则执行并回填 →
    再次推理生成自然语言回复。失败（模型/网络）时优雅降级为友好提示，不抛 500。
    """
    # 1) 组装上下文（近 7 天 + 语义记忆；embedding 未启用时自动降级）
    try:
        ctx = await build_context(session, current.id, days=7, use_semantic=True)
    except Exception:
        ctx = "（暂无可用的近期数据）"

    # 图片联动：有图先建饮食记录，并把结果注记进用户消息
    image_note = ""
    if payload.image_base64:
        image_note = await _handle_image(payload, session, current.id)

    user_text = (payload.message or "").strip()
    user_content = (user_text + "\n" + image_note).strip()

    system = COACH_SYSTEM_PROMPT + "\n\n【用户近期数据】\n" + (ctx or "（暂无记录）")
    messages: list[Message] = [Message(role="system", content=system)]
    # 2) 带入最近若干轮历史（避免上下文过长）
    for h in (payload.history or [])[-6:]:
        role = h.get("role")
        content = h.get("content")
        if role in ("user", "assistant") and content:
            messages.append(Message(role=role, content=content))
    messages.append(Message(role="user", content=user_content))

    actions: list[str] = []
    # 3) 工具调用循环（最多 3 轮，防止异常死循环）
    res: LLMResult = await reason_with_tools(messages, TOOLS)
    for _ in range(3):
        if not res.ok or not res.tool_calls:
            break
        # 把模型的工具调用声明作为 assistant 消息保留
        messages.append(
            Message(role="assistant", content=res.text, tool_calls=res.tool_calls)
        )
        for tc in res.tool_calls:
            outcome = await execute_tool(tc.name, tc.arguments, session, current.id)
            actions.append(outcome)
            messages.append(
                Message(role="tool", content=outcome, tool_call_id=tc.id, name=tc.name)
            )
        # 再次推理，生成带动作结果的自然语言回复
        res = await reason(messages)

    if not res.ok:
        return ChatOut(
            reply="抱歉，教练这会儿有点忙，稍后再来聊吧～（若持续出现可检查模型配置）",
            ok=False,
            actions=actions,
        )
    return ChatOut(reply=res.text or "（教练没有返回内容）", ok=True, actions=actions)
