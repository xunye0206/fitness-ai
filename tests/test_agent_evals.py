"""P0-2 行为化 Eval：让 Agent 的「意图→工具→参数」可度量（对照 OpenCode 维度）。

全部用假 LLM（ScriptedStream 驱动 AgentLoop），确定性、零 token、零 key。
覆盖：
① 意图→工具→参数正确（跑步 → log_training，参数透传）
② 闲聊不调工具（无 action 事件）
③ 护栏拦截破坏性工具（无 confirmed 时不真跑）
④ 预算上限触发强制收尾（不卡死、仍产出回复）
⑤ 参数类型强转（"30" 字符串 → int 后进入 run）

说明：模型「理解意图选对工具」这一步靠真模型，由单独的「真实模型冒烟」覆盖；
本套件确定性地验证 AgentLoop 的工具分发 / 校验 / 护栏 / 预算 管线的正确性。
"""
import asyncio
from typing import Awaitable, Callable, List

from app.agent.loop import AgentLoop
from app.agent.registry import ToolRegistry
from app.agent.guardrails_coach import CoachToolGuardrail
from app.agent.tool import Tool, ToolContext, ToolResult
from app.agent.types import AgentEvent
from app.llm.base import Message, ToolCall


class CaptureTool(Tool):
    """记录被调用时收到的 (name, arguments)，不碰数据库。"""

    calls: List[tuple] = []

    def __init__(self, name, params=None, destructive=False, write=False):
        self._n = name
        self._p = params or {"properties": {}, "required": []}
        self._d = destructive
        self._w = write

    @property
    def name(self):
        return self._n

    @property
    def description(self):
        return "cap"

    def parameters_schema(self):
        return self._p

    def is_destructive(self):
        return self._d

    def is_write(self):
        return self._w

    async def run(self, arguments, ctx):
        CaptureTool.calls.append((self._n, arguments))
        return ToolResult(content=f"ok:{self._n}")


class ScriptedStream:
    """假 LLM：按预设脚本驱动 AgentLoop。

    脚本元素：("tools", [ToolCall...]) 表示该轮发起工具；
                 ("delta", "文本")         表示该轮只输出文本（最终回复）。
    轮次自增消费脚本；耗尽后一律返回 delta（收尾）。
    """

    def __init__(self, script: list):
        self.script = list(script)
        self.i = 0

    def _next(self):
        if self.i < len(self.script):
            item = self.script[self.i]
            self.i += 1
            return item
        return ("delta", "（收尾）好的。")

    async def stream_with_tools(self, messages, tools, tool_choice="auto"):
        kind, payload = self._next()
        if kind == "tools":
            yield {"type": "tools", "calls": payload}
        else:
            yield {"type": "delta", "text": payload}

    async def stream_final(self, messages, tools=None):
        kind, payload = self._next()
        if kind == "tools":  # 收尾不该再要工具
            yield "（收尾）好的。"
        else:
            yield payload


def _build_loop(script, reg, guardrail=None, budget=None):
    ss = ScriptedStream(script)
    loop = AgentLoop(
        stream_with_tools=ss.stream_with_tools,
        stream_final=ss.stream_final,
        registry=reg,
        max_iterations=4,
        guardrail=guardrail,
        budget_tokens=budget,
    )
    return loop


def _run_loop(loop, history):
    async def _go():
        return [e async for e in loop.run(history, ToolContext(user_id=1))]

    return asyncio.run(_go())


def test_eval_intent_to_tool_and_params():
    CaptureTool.calls = []
    reg = ToolRegistry()
    reg.register(CaptureTool(
        "log_training",
        {"properties": {"exercise_type": {"type": "string"}, "duration_min": {"type": "integer"}},
         "required": ["exercise_type", "duration_min"]},
        write=True,
    ))
    loop = _build_loop(
        [("tools", [ToolCall(id="t1", name="log_training",
                        arguments={"exercise_type": "跑步", "duration_min": 30})])],
        reg,
    )
    evs = _run_loop(loop, [Message(role="user", content="我刚跑了5公里")])
    assert ("log_training", {"exercise_type": "跑步", "duration_min": 30}) in CaptureTool.calls
    assert any(e.type == "action" and "ok:log_training" in e.text for e in evs)


def test_eval_chat_no_tool():
    CaptureTool.calls = []
    reg = ToolRegistry()
    reg.register(CaptureTool("log_training", write=True))
    loop = _build_loop([("delta", "你好，我是你的教练。")], reg)
    evs = _run_loop(loop, [Message(role="user", content="你好")])
    assert not any(e.type == "action" for e in evs)
    assert any(e.type == "delta" for e in evs)


def test_eval_guardrail_denies_destructive():
    CaptureTool.calls = []
    reg = ToolRegistry()
    reg.register(CaptureTool("delete_all", destructive=True))
    g = CoachToolGuardrail(reg)
    loop = _build_loop(
        [("tools", [ToolCall(id="t1", name="delete_all", arguments={})])],
        reg, guardrail=g,
    )
    evs = _run_loop(loop, [Message(role="user", content="清空")])
    assert ("delete_all", {}) not in CaptureTool.calls  # 工具没真跑
    assert any(e.type == "action" and "护栏拦截" in e.text for e in evs)


def test_eval_budget_force_finalize():
    CaptureTool.calls = []
    reg = ToolRegistry()
    reg.register(CaptureTool("log_training", write=True))
    # 超长首条消息 → 第一轮输入就超 tiny 预算 → 直接收尾，不跑工具
    loop = _build_loop(
        [("tools", [ToolCall(id="t1", name="log_training",
                        arguments={"exercise_type": "跑步", "duration_min": 30})])],
        reg, budget=10,
    )
    evs = _run_loop(loop, [Message(role="user", content="x" * 100)])
    assert CaptureTool.calls == []  # 预算拦截，工具未执行
    assert any(e.type == "delta" for e in evs)  # 仍产出收尾回复（不卡死）


def test_eval_param_coercion_runs():
    CaptureTool.calls = []
    reg = ToolRegistry()
    reg.register(CaptureTool(
        "log_training",
        {"properties": {"exercise_type": {"type": "string"}, "duration_min": {"type": "integer"}},
         "required": ["exercise_type", "duration_min"]},
        write=True,
    ))
    # 模型把 duration_min 写成字符串 "30" → 校验应强转为 int 再进 run
    loop = _build_loop(
        [("tools", [ToolCall(id="t1", name="log_training",
                        arguments={"exercise_type": "跑步", "duration_min": "30"})])],
        reg,
    )
    _run_loop(loop, [Message(role="user", content="跑步30分")])
    assert CaptureTool.calls
    assert CaptureTool.calls[0][1]["duration_min"] == 30  # 被强转为 int
    assert isinstance(CaptureTool.calls[0][1]["duration_min"], int)
