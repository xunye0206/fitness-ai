"""Agent 核心循环（对应 OpenCode 的 agent.go processGeneration）。

这是整个 agent 的心脏，也是旧版最「糙」的地方——旧版只在 API 里做了「单次」
工具检测：模型一轮发起工具调用就执行一次、然后直接出最终回复，无法多轮联动
（例如先查报告再据其给建议）。这里改成真正可迭代的循环：

    for 每一轮（上限 max_iterations）:
        流式调 LLM（带工具 schema），边收边把文本 delta 推出去
        若模型发起工具调用 → 经注册表 + 护栏执行 → 结果作为 tool 消息回灌
        若没有工具调用 → 说明最终回复已通过 delta 流式输出，结束
    若一直有工具调用直到触顶 → 用 stream_final 强制收尾，避免无限烧钱

关键设计（依赖注入）：循环本身不 import 任何 LLM 实现，stream_with_tools /
stream_final 由调用方注入（api.py 注入 router 的 reason_stream_with_tools /
reason_stream）。这样循环可脱离真实模型单测（注入假函数即可），也方便测试 monkeypatch。

本文件还集中承载几个「扛真实世界」的能力（对照 OpenCode 维度）：
- 可靠性：stream_with 外包一层重试（指数退避），网络抖一下不致命。
- 成本控制：每轮 LLM 调用前估算输入 token，超 budget_tokens 则强制收尾；
  累计 total_input_tokens，供上层打印近似成本（烧钱可见）。
- 上下文工程：回灌给模型的 tool 结果做字符截断，避免多轮把上下文撑爆。
- 可观测：debug 开启时每轮 logger.debug 打印消息数/输入 token/工具数
  （对应 OpenCode 的 debug 日志 + 消息落盘）。
"""
import asyncio
import logging
from typing import AsyncIterator, Awaitable, Callable, Optional

from app.agent.registry import ToolRegistry
from app.agent.tool import ToolGuardrail
from app.agent.types import AgentEvent, ToolContext
from app.llm.base import Message, ToolCall

logger = logging.getLogger("fitness_agent.agent")

# 回灌给模型的 tool 结果最大字符数（对应 OpenCode 的 tool result 截断 30k，
# 健身场景单条不必那么长，设 4k 足够；对用户的 action 事件保留全文）。
MAX_TOOL_RESULT_CHARS = 4000
# 单次 LLM 调用的最大重试次数（可靠性）
MAX_STREAM_RETRIES = 3


class AgentLoop:
    def __init__(
        self,
        *,
        stream_with_tools: Callable[..., Awaitable[AsyncIterator[dict]]],
        stream_final: Callable[..., Awaitable[AsyncIterator[str]]],
        registry: ToolRegistry,
        max_iterations: int = 6,
        guardrail: Optional[ToolGuardrail] = None,
        budget_tokens: Optional[int] = None,
        debug: bool = False,
    ) -> None:
        self.stream_with = stream_with_tools
        self.stream_final = stream_final
        self.registry = registry
        self.max_iterations = max_iterations
        self.guardrail = guardrail
        self.budget_tokens = budget_tokens
        self.debug = debug
        # 累计本轮回所有 LLM 调用的输入 token 估算（每次调用都重发全量历史，
        # 所以累加才是真实成本；供上层打印近似 cost）。
        self.total_input_tokens: int = 0

    async def run(self, messages: list[Message], ctx: ToolContext) -> AsyncIterator[AgentEvent]:
        """执行 agent 循环，逐条 yield AgentEvent（delta / action）。

        ctx 可携带 request_id 用于跨日志串联。
        """
        history = list(messages)
        rid = getattr(ctx, "request_id", None) or "-"

        for _ in range(self.max_iterations):
            # —— 成本控制：本轮回灌前的输入 token 估算 ——
            input_tokens = sum(_msg_tokens(m) for m in history)
            self.total_input_tokens += input_tokens
            if self.debug:
                logger.debug(
                    "loop rid=%s round-input≈%d total≈%d n_msgs=%d",
                    rid, input_tokens, self.total_input_tokens, len(history),
                )
            if self.budget_tokens and input_tokens > self.budget_tokens:
                logger.warning(
                    "Agent 触达 token 预算(%d)，强制收尾避免烧钱 rid=%s",
                    self.budget_tokens, rid,
                )
                async for chunk in self._stream_final_with_retry(history, rid):
                    if chunk:
                        yield AgentEvent("delta", chunk)
                return

            # —— 流式推理 + 工具检测（带重试）——
            tool_calls: list = []
            async for ev in self._stream_with_retry(history, rid):
                if ev.get("type") == "delta":
                    if ev.get("text"):
                        yield AgentEvent("delta", ev["text"])
                elif ev.get("type") == "tools":
                    tool_calls = ev.get("calls") or []

            if not tool_calls:
                # 这一轮没有工具调用 → 最终回复已通过上面的 delta 流式输出
                return

            # —— 执行工具并回填，进入下一轮 ——
            history.append(Message(role="assistant", content="", tool_calls=tool_calls))
            for tc in tool_calls:
                name = _field(tc, "name")
                arguments = _field(tc, "arguments") or {}
                tc_id = _field(tc, "id")
                if not name:
                    # 丢弃无名字的幽灵工具调用（DeepSeek 流式常见空占位）
                    continue
                result = await self.registry.execute(name, arguments, ctx, self.guardrail)
                # 无论成功/失败都推给前端（失败也让人知道发生了什么）
                yield AgentEvent("action", result.content)
                # 回灌模型时截断超长 tool 结果，避免撑爆上下文；
                # 但推给用户的 action 事件保留全文。
                model_content = result.content
                if len(model_content) > MAX_TOOL_RESULT_CHARS:
                    model_content = (
                        model_content[:MAX_TOOL_RESULT_CHARS]
                        + f"\n…[工具结果已截断，原文 {len(result.content)} 字]"
                    )
                history.append(
                    Message(
                        role="tool",
                        content=model_content,
                        tool_call_id=tc_id,
                        name=name,
                    )
                )
            # 回到循环顶部：让模型基于工具结果决定「再调工具」还是「给最终回复」

        # 触达 max_iterations 仍未自然结束（模型一直要调工具）→ 强制收尾
        logger.warning("Agent 触达 max_iterations=%d，强制收尾避免死循环 rid=%s",
                      self.max_iterations, rid)
        async for chunk in self._stream_final_with_retry(history, rid):
            if chunk:
                yield AgentEvent("delta", chunk)

    # —— 重试封装（可靠性）——
    async def _stream_with_retry(self, history: list[Message], rid: str) -> AsyncIterator[dict]:
        """对带工具的流式调用做有限次重试（指数退避）。

        整段流失败才重试（重新发起一次完整调用）；全部失败后推一段
        降级 delta，让上层走 force-finalize，不抛异常中断主链路。
        """
        last_exc: Optional[Exception] = None
        for attempt in range(MAX_STREAM_RETRIES):
            try:
                async for ev in self.stream_with(history, self.registry.schemas()):
                    yield ev
                return
            except Exception as exc:  # 流式过程异常（网络/超时/供应商 5xx）
                last_exc = exc
                if attempt < MAX_STREAM_RETRIES - 1:
                    backoff = min(0.5 * (2 ** attempt), 4.0)
                    logger.warning("stream_with 第 %d 次失败，%.1fs 后重试 rid=%s: %s",
                                  attempt + 1, backoff, rid, exc)
                    await asyncio.sleep(backoff)
                    continue
        logger.error("stream_with 重试 %d 次仍失败，降级处理 rid=%s: %s",
                     MAX_STREAM_RETRIES, rid, last_exc)
        yield {"type": "delta", "text": "\n[模型调用重试多次仍失败，已降级处理]"}

    async def _stream_final_with_retry(self, history: list[Message], rid: str) -> AsyncIterator[str]:
        last_exc: Optional[Exception] = None
        for attempt in range(MAX_STREAM_RETRIES):
            try:
                async for chunk in self.stream_final(history):
                    yield chunk
                return
            except Exception as exc:
                last_exc = exc
                if attempt < MAX_STREAM_RETRIES - 1:
                    backoff = min(0.5 * (2 ** attempt), 4.0)
                    logger.warning("stream_final 第 %d 次失败，%.1fs 后重试 rid=%s: %s",
                                  attempt + 1, backoff, rid, exc)
                    await asyncio.sleep(backoff)
                    continue
        logger.error("stream_final 重试 %d 次仍失败 rid=%s: %s",
                     MAX_STREAM_RETRIES, rid, last_exc)
        yield ""


def _msg_tokens(m: Message) -> int:
    """粗略估算单条消息的 token 数（无 tokenizer，按字符 /2 估保守上限）。

    仅用于预算判断，不追求精确；真实成本以供应商 usage 为准（如可得）。
    """
    base = len(m.content or "") // 2
    extra = sum(len(str(t)) for t in (m.tool_calls or [])) // 4
    return max(1, base + extra)


def _field(tc, key):
    """兼容 ToolCall 数据类与 dict 两种形态（不同 provider 可能给 dict）。"""
    if isinstance(tc, dict):
        return tc.get(key)
    return getattr(tc, key, None)
