"""Tool 接口 + ToolRegistry 单元测试（纯逻辑，零网络）。

验证：注册/取用、OpenAI function schema 导出、成功执行、未知工具优雅降级、
工具抛异常被兜底、护栏可在执行前拦截。
"""
import asyncio

from app.agent.registry import ToolRegistry
from app.agent.tool import Tool, ToolGuardrail
from app.agent.types import ToolContext, ToolResult


class AddTool(Tool):
    @property
    def name(self):
        return "add"

    @property
    def description(self):
        return "两数相加"

    def parameters_schema(self):
        return {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}

    async def run(self, arguments, ctx):
        return ToolResult(content=str(arguments["a"] + arguments["b"]))


class BoomTool(Tool):
    @property
    def name(self):
        return "boom"

    @property
    def description(self):
        return "x"

    def parameters_schema(self):
        return {"type": "object", "properties": {}}

    async def run(self, arguments, ctx):
        raise RuntimeError("kaboom")


async def _execute(reg, name, args, ctx, guardrail=None):
    return await reg.execute(name, args, ctx, guardrail)


def test_register_and_get():
    reg = ToolRegistry()
    t = AddTool()
    reg.register(t)
    assert reg.get("add") is t
    assert reg.get("nope") is None


def test_schemas_openai_format():
    reg = ToolRegistry()
    reg.register(AddTool())
    schemas = reg.schemas()
    assert len(schemas) == 1
    fn = schemas[0]["function"]
    assert fn["name"] == "add"
    assert fn["parameters"]["type"] == "object"
    assert "a" in fn["parameters"]["properties"]


def test_execute_success():
    reg = ToolRegistry()
    reg.register(AddTool())
    res = asyncio.run(_execute(reg, "add", {"a": 2, "b": 3}, ToolContext(user_id=1, session=None)))
    assert res.content == "5"
    assert res.is_error is False


def test_execute_unknown_tool_graceful():
    reg = ToolRegistry()
    res = asyncio.run(_execute(reg, "ghost", {}, ToolContext(user_id=1, session=None)))
    assert res.is_error is True
    assert "未知" in res.content


def test_execute_tool_exception_caught():
    reg = ToolRegistry()
    reg.register(BoomTool())
    res = asyncio.run(_execute(reg, "boom", {}, ToolContext(user_id=1, session=None)))
    assert res.is_error is True
    assert "kaboom" in res.content


def test_guardrail_denies_before_execution():
    class DenyAll(ToolGuardrail):
        async def check(self, name, arguments, ctx):
            return "该工具被护栏禁止"

    reg = ToolRegistry()
    reg.register(AddTool())
    res = asyncio.run(_execute(reg, "add", {"a": 1, "b": 1}, ToolContext(user_id=1, session=None), guardrail=DenyAll()))
    assert res.is_error is True
    assert "护栏" in res.content
