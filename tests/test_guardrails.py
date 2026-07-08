"""里程碑2：输出合规护栏单元测试（纯函数，零网络）。

验证 check_output_safety 拦截越界医疗措辞、放行正常建议、忽略否定语境的边界声明；
needs_disclaimer 对已含免责声明的回复返回 True 避免重复；llm_check_output_safety
语义复核能补漏关键词黑名单的改写绕过，且调用失败时降级放行。
"""
import asyncio

from app.agent.guardrails import (
    DISCLAIMER_TEXT,
    check_output_safety,
    llm_check_output_safety,
    needs_disclaimer,
)
from app.llm.base import LLMResult


def test_check_output_safety_allows_normal_advice():
    v = check_output_safety("今晚练胸，做4组卧推，每组10次，组间休息60秒。")
    assert v.allowed is True
    assert v.reasons == []


def test_check_output_safety_flags_forbidden_medical_claim():
    v = check_output_safety("根据你的指标，你患有轻度高血脂，建议服用他汀类药物。")
    assert v.allowed is False
    assert any("你患有" in r for r in v.reasons)
    assert any("建议服用" in r for r in v.reasons)


def test_check_output_safety_ignores_negation_boundary():
    # 模型声明自己做不了诊断，属边界声明，不应判为越界
    v = check_output_safety("我不是医生，不能做医疗诊断，如有不适请就医。")
    assert v.allowed is True


def test_check_output_safety_empty_passes():
    assert check_output_safety("").allowed is True


def test_needs_disclaimer_true_when_present():
    assert needs_disclaimer("以上建议不构成医疗诊断，请咨询医师。") is True


def test_needs_disclaimer_false_when_absent():
    assert needs_disclaimer("今晚练腿，做深蹲。") is False


def test_disclaimer_text_mentions_non_medical():
    assert "不构成医疗诊断" in DISCLAIMER_TEXT


async def _fake_reason_unsafe(messages):
    return LLMResult(text="UNSAFE：暗示疾病诊断与调理用药")


async def _fake_reason_safe(messages):
    return LLMResult(text="SAFE")


async def _fake_reason_error(messages):
    raise RuntimeError("boom")


def test_llm_check_flags_rewritten_bypass():
    # 关键词层放过的改写绕过，LLM 语义复核应拦下
    v = asyncio.run(
        llm_check_output_safety("你这指标看着像高血脂，调一调代谢就好。", _fake_reason_unsafe)
    )
    assert v.allowed is False


def test_llm_check_allows_normal():
    v = asyncio.run(llm_check_output_safety("今晚练背，做4组引体。", _fake_reason_safe))
    assert v.allowed is True


def test_llm_check_degrades_on_error():
    # 复核调用失败 → 降级放行，不阻断主链路
    v = asyncio.run(llm_check_output_safety("任意文本", _fake_reason_error))
    assert v.allowed is True
