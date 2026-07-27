"""P0-1 / P1-3 单元测试：参数校验 + 默认工具护栏。

全部用假 Tool，零网络零 key，确定性。
覆盖：类型强转、必填报检、类型错误；护栏的「正常写放行 / 破坏性需确认 /
单轮回写上限 / 黑名单 / registry.execute 真正走校验」。
"""
import asyncio

from app.agent.guardrails_coach import CoachToolGuardrail
from app.agent.registry import ToolRegistry
from app.agent.tool import Tool, ToolContext, ToolResult
from app.agent.validation import validate_arguments


# ---------------- 参数校验 ----------------
def test_validate_coerces_string_int():
    schema = {"properties": {"n": {"type": "integer"}}, "required": ["n"]}
    ok, c, err = validate_arguments(schema, {"n": "30"})
    assert ok and c["n"] == 30 and isinstance(c["n"], int)


def test_validate_missing_required():
    schema = {"properties": {"n": {"type": "integer"}}, "required": ["n"]}
    ok, _, err = validate_arguments(schema, {})
    assert not ok and "缺少必填" in err


def test_validate_bad_type():
    schema = {"properties": {"n": {"type": "integer"}}, "required": ["n"]}
    ok, _, err = validate_arguments(schema, {"n": "abc"})
    assert not ok and "应为 integer" in err


# ---------------- 护栏 ----------------
class _FakeTool(Tool):
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
        return "x"

    def parameters_schema(self):
        return self._p

    def is_destructive(self):
        return self._d

    def is_write(self):
        return self._w

    async def run(self, arguments, ctx):
        return ToolResult(content="ran")


def _ctx():
    return ToolContext(user_id=1)


def test_guardrail_allows_normal_write():
    async def run():
        reg = ToolRegistry()
        reg.register(_FakeTool("log_x", write=True))
        g = CoachToolGuardrail(reg, max_writes_per_request=10)
        assert await g.check("log_x", {}, _ctx()) is None

    asyncio.run(run())


def test_guardrail_denies_destructive_without_confirm():
    async def run():
        reg = ToolRegistry()
        reg.register(_FakeTool("del_x", destructive=True))
        g = CoachToolGuardrail(reg)
        assert await g.check("del_x", {}, _ctx()) is not None
        assert await g.check("del_x", {"confirmed": True}, _ctx()) is None

    asyncio.run(run())


def test_guardrail_enforces_write_limit():
    async def run():
        reg = ToolRegistry()
        reg.register(_FakeTool("log_x", write=True))
        g = CoachToolGuardrail(reg, max_writes_per_request=2)
        assert await g.check("log_x", {}, _ctx()) is None
        assert await g.check("log_x", {}, _ctx()) is None
        assert await g.check("log_x", {}, _ctx()) is not None  # 第 3 次被拦

    asyncio.run(run())


def test_guardrail_blacklist():
    from app.agent.guardrails_coach import DENY_LIST

    async def run():
        reg = ToolRegistry()
        reg.register(_FakeTool("evil"))
        DENY_LIST.add("evil")
        try:
            g = CoachToolGuardrail(reg)
            assert await g.check("evil", {}, _ctx()) is not None
        finally:
            DENY_LIST.discard("evil")

    asyncio.run(run())


def test_registry_execute_uses_validation():
    async def run():
        reg = ToolRegistry()
        # 带必填项 n 的工具；传 {} → 校验失败，不调用 run
        reg.register(_FakeTool("log_x", params={"properties": {"n": {"type": "integer"}}, "required": ["n"]}))
        res = await reg.execute("log_x", {}, ToolContext(user_id=1))
        assert res.is_error and "参数错误" in res.content

    asyncio.run(run())
