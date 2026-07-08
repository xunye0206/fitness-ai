"""里程碑2：输出合规护栏单元测试（纯函数，零网络）。

验证 check_output_safety 拦截越界医疗措辞、放行正常建议、忽略否定语境的边界声明；
以及 needs_disclaimer 对已含免责声明的回复返回 True 以避免重复追加。
"""
from app.agent.guardrails import (
    DISCLAIMER_TEXT,
    check_output_safety,
    needs_disclaimer,
)


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
