"""agent 横切层对外接口：AI 教练对话（支持联动其他模块）。

区别于功能模块（diet/training/...），对话是 agent 的核心交互面——用户直接对教练
说话，教练基于「近期数据 + 护栏」作答；当用户意图是「记饮食/记训练/出报告」时，
教练通过工具调用真正去执行这些动作。

响应采用 SSE 流式输出：教练回复逐字推送给前端，避免「沉默等待」的体感卡顿。

本文件只负责「编排」：组装上下文 → 交给 AgentLoop 跑（多轮工具循环）→ 合规护栏 →
推送。真正的循环逻辑在 app.agent.loop，工具在 app.agent.coach_tools，agent 定义
在 app.agent.agent。LLM 调用通过依赖注入进入循环（stream_with_tools /
stream_final），因此本模块的同名函数可被测试 monkeypatch。
"""
import json
import logging
import os
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.agent.agent import build_coach_agent
from app.agent.context import build_context
from app.agent.graph import run_diet_recognition
from app.agent.graph_training import run_training_recognition
from app.agent.guardrails import (
    DISCLAIMER_TEXT,
    GuardrailVerdict,
    LLM_COMPLIANCE_CHECK,
    check_output_safety,
    llm_check_output_safety,
    needs_disclaimer,
)
from app.agent.guardrails_coach import CoachToolGuardrail
from app.agent.loop import AgentLoop
from app.agent.profile import recall_profile, schedule_profile_update
from app.agent.session import SessionManager, SqliteSessionStore, StoredMessage
from app.agent.summarize import summarize_history
from app.agent.tools import REGISTRY
from app.agent.types import ToolContext
from app.llm.base import Message
from app.llm.router import reason, reason_stream, reason_stream_with_tools
from app.modules.auth.api import get_current_user
from app.modules.auth.domain import User
from app.modules.diet.domain import DietEntry

logger = logging.getLogger("fitness_agent.agent")

router = APIRouter(prefix="/agent", tags=["agent"])

# 会话依赖（与 diet/training/... 模块一致的注入方式）
SessionDep = Annotated[AsyncSession, Depends(get_session)]

# 进程级教练 agent（声明式定义 + 工具注册表），单一实例复用
COACH_AGENT = build_coach_agent(REGISTRY)
COACH_SYSTEM_PROMPT = COACH_AGENT.system_prompt

# 工具护栏（执行层硬边界，弥补旧版 guardrail=None 的缺口）
COACH_GUARDRAIL = CoachToolGuardrail(REGISTRY, max_writes_per_request=10)

# 服务端会话管理（零成本 SQLite 持久，重启不丢；前端可选带 session_id 走服务端记忆）
_SESSION_STORE = SqliteSessionStore()
SESSION_MANAGER = SessionManager(_SESSION_STORE, max_tokens=6000, summarizer=summarize_history)

# Agent 成本控制 / 可观测开关（环境变量可调，默认不开 debug、预算给较大值）
AGENT_BUDGET_TOKENS = int(os.getenv("AGENT_BUDGET_TOKENS", "80000"))
AGENT_DEBUG = os.getenv("AGENT_DEBUG", "false").lower() in ("1", "true", "yes", "是")


class ChatIn(BaseModel):
    message: str
    history: list[dict] = []  # 前端维护的对话历史：[{"role":"user|assistant","content":"..."}]
    image_base64: Optional[str] = None  # 可选：用户发来的图片（base64，不含 data: 前缀）
    image_mode: Optional[str] = "diet"  # 图片类型：diet=食物(默认) / training=训练截图
    session_id: Optional[str] = None  # 可选：服务端会话 id（带则走服务端记忆）


async def _handle_image(payload: ChatIn, session: AsyncSession, user_id: int) -> dict:
    """处理用户发的图片，按 image_mode 分流，返回 {note, status, training?}。"""
    mode = (payload.image_mode or "diet").lower()
    if mode == "training":
        return await _handle_training_image(payload, user_id)
    return await _handle_diet_image(payload, session, user_id)


async def _handle_diet_image(payload: ChatIn, session: AsyncSession, user_id: int) -> dict:
    """饮食图片：自动建记录，返回给教练点评的注记。"""
    b64_len = len(payload.image_base64 or "")
    logger.info("用户 %d 发来饮食图片，base64 长度 %d 字符", user_id, b64_len)
    try:
        rec = await run_diet_recognition(user_id, payload.image_base64)
    except Exception as exc:
        logger.warning("视觉识别异常：%s", exc, exc_info=True)
        return {"note": "（用户发来一张食物图片，但视觉识别调用失败）", "status": "⚠️ 图片识别未成功"}
    est = rec.get("recognition")
    verdict = rec.get("verdict")
    if est is None:
        log_lines = rec.get("log", [])
        logger.warning("视觉识别未返回有效结果，graph日志=%s", log_lines)
        return {
            "note": "（用户发来一张食物图片，但视觉识别未成功，建议用户稍后手动补充）",
            "status": "⚠️ 图片识别未成功，已转为文字记录模式",
        }
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
    return {"note": note, "status": "✅ 图片已识别"}


async def _handle_training_image(payload: ChatIn, user_id: int) -> dict:
    """训练截图：识别但不落库，返回结构化结果供前端确认卡。"""
    b64_len = len(payload.image_base64 or "")
    logger.info("用户 %d 发来训练截图，base64 长度 %d 字符", user_id, b64_len)
    try:
        rec = await run_training_recognition(user_id, payload.image_base64)
    except Exception as exc:
        logger.warning("训练视觉识别异常：%s", exc, exc_info=True)
        return {"note": "（用户发来一张训练截图，但视觉识别调用失败）", "status": "⚠️ 图片识别未成功"}
    est = rec.get("recognition")
    verdict = rec.get("verdict")
    if est is None:
        logger.warning("训练识别未返回有效结果，graph日志=%s", rec.get("log", []))
        return {
            "note": "（用户发来一张训练截图，但识别未成功，建议用户稍后手动补充或去训练页录入）",
            "status": "⚠️ 训练截图识别未成功",
        }
    needs_confirm = bool(verdict and verdict.needs_confirmation)
    recognition = {
        "estimate": est.model_dump(),
        "needs_confirmation": needs_confirm,
        "guardrail_reasons": verdict.reasons if verdict else [],
    }
    note = (
        f"（用户发来一张训练截图，已识别为训练数据：{est.exercise_type} "
        f"约 {est.duration_min:.0f} 分钟，已生成确认卡片，等待用户确认保存）"
    )
    return {"note": note, "status": "✅ 训练截图已识别", "training": recognition}


@router.post("/chat")
async def chat(payload: ChatIn, session: SessionDep, current: User = Depends(get_current_user)) -> StreamingResponse:
    """AI 教练对话（多轮工具循环，SSE 流式输出）。

    流程：组装上下文 → AgentLoop（流式推理 + 工具调用 + 回填，可多轮）→ 合规护栏 +
    免责声明。失败（模型/网络）时优雅降级为友好提示，不抛 500。
    """
    # 1) 组装上下文（近 7 天窗口；聊天场景关闭实时语义 embedding，省一轮网络往返）
    try:
        ctx_text = await build_context(session, current.id, days=7, use_semantic=False)
    except Exception:
        ctx_text = "（暂无可用的近期数据）"

    system = COACH_SYSTEM_PROMPT + "\n\n【用户近期数据】\n" + (ctx_text or "（暂无记录）")
    messages: list[Message] = [Message(role="system", content=system)]

    # 2) 历史：带 session_id → 服务端记忆；否则兼容前端 history（取最近 4 轮）
    if payload.session_id:
        stored = await SESSION_MANAGER.get_history(payload.session_id)
        for m in stored:
            messages.append(
                Message(
                    role=m.role,
                    content=m.content,
                    tool_calls=m.tool_calls,
                    tool_call_id=m.tool_call_id,
                    name=m.name,
                )
            )
    else:
        for h in (payload.history or [])[-4:]:
            role = h.get("role")
            content = h.get("content")
            if role in ("user", "assistant") and content:
                messages.append(Message(role=role, content=content))
    # 注：用户消息（含图片处理结果）在 event_gen 内部追加，
    # 因为图片识别是耗时操作，需要在 SSE 流中先推送状态再拼接消息。

    request_id = uuid.uuid4().hex  # 本次对话唯一 id，贯穿日志追踪

    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    async def event_gen():
        actions: list[str] = []
        full_reply = ""  # 累积教练最终回复，用于输出合规检测
        try:
            # 2.5) 图片处理（流式推理之前同步完成；通过 SSE 通知前端状态）
            if payload.image_base64:
                yield _sse({"type": "status", "text": "正在识别图片…"})
                img_res = await _handle_image(payload, session, current.id)
                image_note = img_res.get("note", "")
                yield _sse({"type": "status", "text": img_res.get("status", "")})
                if img_res.get("training"):
                    yield _sse({"type": "training_recognition", "recognition": img_res["training"]})
            else:
                image_note = ""

            user_text = (payload.message or "").strip()
            user_content = (user_text + "\n" + image_note).strip()

            # 2.7) 长期画像召回：用用户当前发言定向语义召回，注入上下文做个性化
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

            # 3) 交给 AgentLoop 跑：多轮工具循环 + 流式输出
            loop = AgentLoop(
                stream_with_tools=reason_stream_with_tools,
                stream_final=reason_stream,
                registry=REGISTRY,
                max_iterations=COACH_AGENT.max_iterations,
                guardrail=COACH_GUARDRAIL,       # 执行层硬边界（黑名单/破坏性确认/回写上限）
                budget_tokens=AGENT_BUDGET_TOKENS,  # 超预算强制收尾，防失控烧钱
                debug=AGENT_DEBUG,                   # 开启后每轮打 debug 日志（可观测）
            )
            tool_ctx = ToolContext(user_id=current.id, session=session, request_id=request_id)
            async for ev in loop.run(messages, tool_ctx):
                if ev.type == "delta":
                    if ev.text:
                        full_reply += ev.text
                        yield _sse({"type": "delta", "text": ev.text})
                elif ev.type == "action":
                    actions.append(ev.text)
                    yield _sse({"type": "action", "text": ev.text})

            logger.info(
                "Agent 对话完成 rid=%s user=%d 近似输入token≈%d",
                request_id, current.id, loop.total_input_tokens,
            )

            # 4) 输出合规检测 + 免责声明
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

            # 5) 长期画像记忆后台更新（fire-and-forget，不阻塞 SSE 流返回）
            schedule_profile_update(current.id, user_text, full_reply)

            # 6) 若带 session_id，把本轮用户发言与最终回复持久化到服务端会话
            if payload.session_id:
                await SESSION_MANAGER.append(
                    payload.session_id, StoredMessage(role="user", content=user_content)
                )
                await SESSION_MANAGER.append(
                    payload.session_id, StoredMessage(role="assistant", content=full_reply)
                )
                # 超预算时压缩为摘要（只在超预算才烧一次 LLM，正常不触发）
                try:
                    await SESSION_MANAGER.compact_if_needed(payload.session_id)
                except Exception as exc:
                    logger.warning("会话压缩失败（已忽略，不影响本次回复）：%s", exc)

            yield _sse({"type": "done", "ok": True})
        except Exception as exc:
            yield _sse({"type": "delta", "text": "出错了：" + str(exc)})
            yield _sse({"type": "done", "ok": False})

    return StreamingResponse(event_gen(), media_type="text/event-stream; charset=utf-8")
