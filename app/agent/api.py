"""agent 横切层暴露的对外接口：AI 教练对话（支持联动其他模块）。

区别于功能模块（diet/training/...），对话是 agent 的核心交互面——
用户直接对教练说话，教练基于「近期数据 + 护栏」作答；
当用户意图是"记饮食/记训练/出报告"时，教练通过工具调用真正去执行这些动作。

响应采用 SSE 流式输出：教练回复逐字推送给前端，避免「沉默等待」的体感卡顿。
"""
import json
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent.graph import run_diet_recognition
from app.agent.tools import TOOLS, execute_tool
from app.core.db import get_session
from app.llm.base import Message
from app.llm.router import reason, reason_stream, reason_stream_with_tools
from app.agent.context import build_context
from app.agent.profile import recall_profile, schedule_profile_update
from app.agent.guardrails import (
    check_output_safety,
    needs_disclaimer,
    DISCLAIMER_TEXT,
    llm_check_output_safety,
    LLM_COMPLIANCE_CHECK,
    GuardrailVerdict,
)
from app.modules.auth.api import get_current_user
from app.modules.auth.domain import User
from app.modules.diet.domain import DietEntry

logger = logging.getLogger("fitness_agent.agent")

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


async def _handle_image(
    payload: ChatIn, session: AsyncSession, user_id: int
) -> str:
    """处理用户发来的食物图片：走视觉管线建饮食记录，返回给教练点评的注记。"""
    b64_len = len(payload.image_base64 or "")
    logger.info("用户 %d 发来图片，base64 长度 %d 字符", user_id, b64_len)
    try:
        rec = await run_diet_recognition(user_id, payload.image_base64)
    except Exception as exc:
        logger.warning("视觉识别异常：%s", exc, exc_info=True)
        return "（用户发来一张食物图片，但视觉识别调用失败）"
    est = rec.get("recognition")
    verdict = rec.get("verdict")
    if est is None:
        log_lines = rec.get("log", [])
        logger.warning("视觉识别未返回有效结果，graph日志=%s", log_lines)
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


@router.post("/chat")
async def chat(payload: ChatIn, session: SessionDep, current: UserDep) -> StreamingResponse:
    """AI 教练对话（含工具调用循环），SSE 流式输出。

    流程：组装上下文 → 流式推理（同时检测工具调用）→ 若模型发起工具调用则执行并回填 →
    再流式生成最终自然语言回复。首 token 尽快到达前端，避免「思考中」长期卡顿。
    失败（模型/网络）时优雅降级为友好提示，不抛 500。
    """
    # 1) 组装上下文（近 7 天窗口；聊天场景关闭实时语义 embedding，省一轮网络往返）
    try:
        ctx = await build_context(session, current.id, days=7, use_semantic=False)
    except Exception:
        ctx = "（暂无可用的近期数据）"

    system = COACH_SYSTEM_PROMPT + "\n\n【用户近期数据】\n" + (ctx or "（暂无记录）")
    messages: list[Message] = [Message(role="system", content=system)]
    # 2) 带入最近若干轮历史（避免上下文过长）
    for h in (payload.history or [])[-4:]:
        role = h.get("role")
        content = h.get("content")
        if role in ("user", "assistant") and content:
            messages.append(Message(role=role, content=content))
    # 注意：用户消息（含图片处理结果）在 event_gen 内部追加，
    # 因为图片识别是耗时操作，需要在 SSE 流中先推送状态再拼接消息。

    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    async def event_gen():
        actions: list[str] = []
        full_reply = ""  # 累积教练最终回复，用于输出合规检测（里程碑2）
        try:
            # 2.5) 图片处理（在流式推理之前，同步完成；通过 SSE 通知前端状态）
            if payload.image_base64:
                yield _sse({"type": "status", "text": "正在识别图片…"})
                image_note = await _handle_image(payload, session, current.id)
                # 如果视觉识别成功，通知前端
                if "但视觉" not in image_note and "调用失败" not in image_note:
                    yield _sse({"type": "status", "text": "✅ 图片已识别"})
                else:
                    yield _sse({"type": "status", "text": "⚠️ 图片识别未成功，已转为文字记录模式"})
            else:
                image_note = ""

            user_text = (payload.message or "").strip()
            user_content = (user_text + "\n" + image_note).strip()

            # 2.7) M10 长期画像召回：用用户当前发言定向语义召回，注入教练上下文做个性化
            try:
                profile_hits = await recall_profile(session, current.id, user_content, k=3)
            except Exception:
                profile_hits = []
            if profile_hits:
                profile_block = "\n".join(f"- {h.text}" for h in profile_hits)
                messages.append(
                    Message(
                        role="system",
                        content="【用户长期画像（仅供参考，用于个性化，勿复述原话）】\n"
                        + profile_block,
                    )
                )

            messages.append(Message(role="user", content=user_content))

            # 3) 流式推理，同时检测工具调用（首 token 立即推送）
            tool_calls = None
            async for ev in reason_stream_with_tools(messages, TOOLS):
                if ev.get("type") == "delta":
                    if ev.get("text"):
                        full_reply += ev["text"]
                        yield _sse({"type": "delta", "text": ev["text"]})
                elif ev.get("type") == "tools":
                    tool_calls = ev.get("calls")

            # 4) 若模型发起工具调用，执行并回填，再流式生成最终回复
            if tool_calls:
                # 过滤无效工具调用（name 为空/None 的中间态片段）
                valid_calls = [tc for tc in tool_calls if tc.name]
                if valid_calls:
                    messages.append(
                        Message(role="assistant", content="", tool_calls=valid_calls)
                    )
                    for tc in valid_calls:
                        outcome = await execute_tool(tc.name, tc.arguments, session, current.id)
                        # 只推送成功的动作给前端（"未知工具/执行失败"不展示）
                        if not outcome.startswith("未知工具") and not outcome.startswith("工具"):
                            actions.append(outcome)
                            yield _sse({"type": "action", "text": outcome})
                        messages.append(
                            Message(role="tool", content=outcome, tool_call_id=tc.id, name=tc.name)
                        )
                # 最终回复流式输出
                async for chunk in reason_stream(messages):
                    if chunk:
                        full_reply += chunk
                        yield _sse({"type": "delta", "text": chunk})

            # 5) 输出合规检测 + 免责声明（里程碑2：对应《策划书》§六/§七、代码规范 §6/§12）
            # 关键词检测 + LLM 语义复核双保险；任一命中越界即补提示，未含免责则补声明。
            safety = check_output_safety(full_reply)
            llm_safety = (
                await llm_check_output_safety(full_reply, reason)
                if LLM_COMPLIANCE_CHECK
                else GuardrailVerdict(allowed=True, reasons=[])
            )
            if not safety.allowed or not llm_safety.allowed:
                hit_reasons = safety.reasons + llm_safety.reasons
                logger.warning("教练回复命中越界措辞 %s，已附合规提示", hit_reasons)
                yield _sse({"type": "delta", "text": "\n\n⚠️ 提醒：我是健身教练，不提供医疗诊断或治疗方案。"})
            if not needs_disclaimer(full_reply):
                yield _sse({"type": "delta", "text": "\n\n" + DISCLAIMER_TEXT})

            # 6) M10 长期画像记忆后台更新：fire-and-forget，不阻塞 SSE 流返回
            schedule_profile_update(current.id, user_text, full_reply)

            yield _sse({"type": "done", "ok": True})
        except Exception as exc:
            yield _sse({"type": "delta", "text": "出错了：" + str(exc)})
            yield _sse({"type": "done", "ok": False})

    return StreamingResponse(event_gen(), media_type="text/event-stream; charset=utf-8")
