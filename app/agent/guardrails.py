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
