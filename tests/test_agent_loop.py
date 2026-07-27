"""AgentLoop 核心循环单元测试（注入假 LLM，零网络）。

验证：① 无工具时直接流式最终回复；② 一轮工具调用→执行→再生成最终回复；
③ 触达 max_iterations 后强制收尾（防死循环）；④ 未知工具不崩溃、优雅降级；
⑤ 护栏可在执行前拦截工具。

本项目测试用同步 def + asyncio.run 驱动（不依赖 pytest-asyncio）。
"""
import asyncio

from app.agent.loop import AgentLoop
from app.agent.registry import ToolRegistry
from app.agent.tool import Tool, ToolGuardrail
from app.agent.types import ToolContext, ToolResult
from app.llm.base import Message, ToolCall


class AddTool(Tool):
    @property
    def name(self):
        return "add"

    @property
    def description(self):
        return "add"

    def parameters_schema(self):
        return {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}

    async def run(self, arguments, ctx):
        return ToolResult(content=f"result={arguments['a'] + arguments['b']}")


class ScriptedStream:
    """按脚本驱动：每调用一次 stream_with_tools 产出一轮（文本或工具调用）。"""

    def __init__(self, turns):
        self.turns = turns
        self.i = 0

    async def stream_with_tools(self, messages, tools, tool_choice="auto"):
        turn = self.turns[self.i]
        self.i += 1
        if isinstance(turn, str):
            yield {"type": "delta", "text": turn}
        else:
            yield {"type": "tools", "calls": turn}

    async def stream_final(self, messages):
        yield "（已达最大轮次，强制收尾）"


async def _run_loop(loop, messages, ctx):
    return [ev async for ev in loop.run(messages, ctx)]


def test_loop_streams_final_answer_without_tools():
    ss = ScriptedStream(["你好，我是你的教练"])
    loop = AgentLoop(stream_with_tools=ss.stream_with_tools, stream_final=ss.stream_final, registry=ToolRegistry())
    evs = asyncio.run(_run_loop(loop, [Message(role="user", content="hi")], ToolContext(1, None)))
    text = "".join(e.text for e in evs if e.type == "delta")
    assert text == "你好，我是你的教练"
    assert all(e.type == "delta" for e in evs)


def test_loop_executes_tool_then_final():
    ss = ScriptedStream([
        [ToolCall(id="c1", name="add", arguments={"a": 2, "b": 3})],
        "已帮你算好：5",
    ])
    reg = ToolRegistry()
    reg.register(AddTool())
    loop = AgentLoop(stream_with_tools=ss.stream_with_tools, stream_final=ss.stream_final, registry=reg)
    evs = asyncio.run(_run_loop(loop, [Message(role="user", content="2+3")], ToolContext(1, None)))
    actions = [e for e in evs if e.type == "action"]
    assert len(actions) == 1
    assert "result=5" in actions[0].text
    assert "已帮你算好：5" in "".join(e.text for e in evs if e.type == "delta")


def test_loop_respects_max_iterations():
    turns = [[ToolCall(id=f"c{i}", name="add", arguments={"a": 1, "b": 1})] for i in range(10)]
    ss = ScriptedStream(turns)
    reg = ToolRegistry()
    reg.register(AddTool())
    loop = AgentLoop(stream_with_tools=ss.stream_with_tools, stream_final=ss.stream_final, registry=reg, max_iterations=3)
    evs = asyncio.run(_run_loop(loop, [Message(role="user", content="x")], ToolContext(1, None)))
    actions = [e for e in evs if e.type == "action"]
    assert len(actions) == 3  # 只执行 3 次，不到无限循环
    assert any("已达最大轮次" in e.text for e in evs if e.type == "delta")


def test_loop_unknown_tool_does_not_crash():
    turns = [[ToolCall(id="c1", name="ghost", arguments={})], "抱歉，换个方式回答"]
    ss = ScriptedStream(turns)
    reg = ToolRegistry()  # ghost 未注册
    loop = AgentLoop(stream_with_tools=ss.stream_with_tools, stream_final=ss.stream_final, registry=reg)
    evs = asyncio.run(_run_loop(loop, [Message(role="user", content="x")], ToolContext(1, None)))
    actions = [e for e in evs if e.type == "action"]
    assert len(actions) == 1
    assert "未知" in actions[0].text
    assert "换个方式" in "".join(e.text for e in evs if e.type == "delta")


def test_loop_guardrail_denies_tool():
    class DenyAdd(ToolGuardrail):
        async def check(self, name, arguments, ctx):
            return "该工具被禁用"

    ss = ScriptedStream([[ToolCall(id="c1", name="add", arguments={"a": 1, "b": 1})], "好的"])
    reg = ToolRegistry()
    reg.register(AddTool())
    loop = AgentLoop(stream_with_tools=ss.stream_with_tools, stream_final=ss.stream_final, registry=reg, guardrail=DenyAdd())
    evs = asyncio.run(_run_loop(loop, [Message(role="user", content="x")], ToolContext(1, None)))
    actions = [e for e in evs if e.type == "action"]
    assert len(actions) == 1
    assert "拦截" in actions[0].text
