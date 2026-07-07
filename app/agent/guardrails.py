"""Agent 护栏（Guardrails）：在动作执行前做量化拦截。

M2 落地两条与饮食相关的规则：
1. 视觉置信度低于阈值 → 标记 needs_confirmation（入库前必须用户确认，不自动采信）。
2. 识别失败（无估算）→ 直接拦截，转人工录入。

后续训练/推送的「伤病信号禁止加练」类规则在此同文件扩展。
"""
from dataclasses import dataclass, field

from app.agent.schemas import FoodEstimate

CONFIDENCE_THRESHOLD = 0.3


@dataclass
class GuardrailVerdict:
    allowed: bool
    needs_confirmation: bool = False
    reasons: list[str] = field(default_factory=list)


class Guardrails:
    def __init__(self, confidence_threshold: float = CONFIDENCE_THRESHOLD) -> None:
        self.confidence_threshold = confidence_threshold

    def evaluate(self, estimate: FoodEstimate | None) -> GuardrailVerdict:
        if estimate is None:
            return GuardrailVerdict(allowed=False, reasons=["视觉识别失败，未获得有效估算"])
        reasons: list[str] = []
        needs_confirmation = False
        if estimate.confidence < self.confidence_threshold:
            needs_confirmation = True
            reasons.append(
                f"视觉置信度 {estimate.confidence:.2f} 低于阈值 "
                f"{self.confidence_threshold:.2f}，需用户确认/修正后方可采信"
            )
        return GuardrailVerdict(allowed=True, needs_confirmation=needs_confirmation, reasons=reasons)


# M4：推送内容护栏
DANGEROUS_PUSH_KEYWORDS = ("加量", "加练", "加强训练", "冲刺", "突破极限")


def push_content_safe(body: str, has_injury: bool) -> GuardrailVerdict:
    """推送内容护栏：有伤病信号时，禁止含「加量/加练」等危险鼓励，改建议休息。

    对应设计稿《策划书》§六：检测到伤病信号 → 禁止「加量」类建议。
    """
    reasons: list[str] = []
    if has_injury and any(k in (body or "") for k in DANGEROUS_PUSH_KEYWORDS):
        reasons.append("检测到伤病信号，已拦截「加量/加练」类危险鼓励，建议休息")
        return GuardrailVerdict(allowed=False, reasons=reasons)
    return GuardrailVerdict(allowed=True, reasons=reasons)
