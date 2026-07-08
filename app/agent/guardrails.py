"""Agent 护栏（Guardrails）：在动作执行前做量化拦截。

M2 落地两条与饮食相关的规则：
1. 视觉置信度低于阈值 → 标记 needs_confirmation（入库前必须用户确认，不自动采信）。
2. 识别失败（无估算）→ 直接拦截，转人工录入。

后续训练/推送的「伤病信号禁止加练」类规则在此同文件扩展。
"""
import logging
from dataclasses import dataclass, field

from app.agent.schemas import FoodEstimate, TrainingEstimate
from app.llm.base import Message

logger = logging.getLogger("fitness_agent.guardrails")

CONFIDENCE_THRESHOLD = 0.3


@dataclass
class GuardrailVerdict:
    allowed: bool
    needs_confirmation: bool = False
    reasons: list[str] = field(default_factory=list)


class Guardrails:
    def __init__(self, confidence_threshold: float = CONFIDENCE_THRESHOLD) -> None:
        self.confidence_threshold = confidence_threshold

    def evaluate(self, estimate: FoodEstimate | TrainingEstimate | None) -> GuardrailVerdict:
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


# 里程碑2：输出合规护栏（对应《策划书》§六/§七、代码规范 §6/§12）
# 健康/健身类产品的合规底线：教练最终回复不得含医疗诊断/治疗类越界措辞，
# 且每次建议需附「非医疗诊断」免责声明。
DISCLAIMER_TEXT = (
    "📌 温馨提示：以上仅为基于你记录数据的健身与营养建议，"
    "不构成医疗诊断或治疗方案。如有不适或健康问题，请咨询专业医师。"
)
# 命中即视为「越界」的明确断言短语（聚焦诊断/治疗行为，避免误伤边界声明）。
# 设计取舍：单字「诊断」「体脂」易与「我不做诊断」混淆，故用行为短语 + 否定语境排除。
FORBIDDEN_OUTPUT_TERMS = (
    "你患有", "您患有", "你得了", "您得了", "疑似患病", "建议服用",
    "开药", "处方药", "康复治疗", "康复方案", "体脂率偏高", "体脂率偏低",
    "病情严重", "临床确诊", "诊断你", "诊断结果", "确诊你",
)
# 否定语境词：命中词前 8 字内出现这些，说明是边界声明（如「我不做诊断」），不算越界。
_NEGATION_NEAR = ("不", "没", "没有", "无法", "不能", "不会", "切勿", "不要")


def _is_negated_context(text: str, pos: int) -> bool:
    """命中词前 8 字内是否是否定语境，是则视为边界声明而非越界断言。"""
    window = text[max(0, pos - 8):pos]
    return any(neg in window for neg in _NEGATION_NEAR)


def check_output_safety(text: str) -> GuardrailVerdict:
    """输出合规检测：扫描教练最终回复是否含越界的医疗/诊断/治疗断言。

    命中（且非否定语境）→ allowed=False，reasons 列出命中短语，交由调用方
    追加合规提示与免责声明；未命中 → allowed=True。
    """
    if not text:
        return GuardrailVerdict(allowed=True, reasons=[])
    reasons: list[str] = []
    for term in FORBIDDEN_OUTPUT_TERMS:
        pos = text.find(term)
        if pos != -1 and not _is_negated_context(text, pos):
            reasons.append(f"回复含越界措辞：{term}")
    if reasons:
        return GuardrailVerdict(allowed=False, reasons=reasons)
    return GuardrailVerdict(allowed=True, reasons=[])


def needs_disclaimer(text: str) -> bool:
    """判断回复是否已自带免责声明，避免重复追加。"""
    markers = ("不构成医疗", "医疗诊断", "咨询专业医师", "免责声明", "非医疗")
    return any(m in (text or "") for m in markers)


# 里程碑2 增强：LLM 语义级合规复核（补漏关键词黑名单的「改写绕过」）
# 对应《策划书》§六/§七、代码规范 §6/§12；作为关键词检测的语义兜底层。
import os

LLM_COMPLIANCE_CHECK = os.getenv("COMPLIANCE_LLM_CHECK", "true").lower() != "false"

COMPLIANCE_PROMPT = (
    "你是健身 App 的合规审核器。判断下面的「教练回复」是否包含医疗诊断、开药处方、"
    "疾病确诊、康复治疗建议等越界内容。普通健身/营养建议，以及声明「我不做诊断」的边界"
    "表述都算安全。只回答一行：安全就回 SAFE；越界就回 UNSAFE，并简要说明命中了什么。\n"
    "教练回复：\n"
)


async def llm_check_output_safety(text: str, reason_fn) -> GuardrailVerdict:
    """LLM 语义级合规复核（补漏关键词黑名单的改写绕过）。

    reason_fn：业务层 reason（app.llm.router.reason），注入以便测试 mock。
    返回 allowed=False 表示 LLM 判为越界。复核调用失败 → 降级放行（不阻断主链路）。
    """
    if not text:
        return GuardrailVerdict(allowed=True, reasons=[])
    messages = [Message(role="user", content=COMPLIANCE_PROMPT + text)]
    try:
        resp = await reason_fn(messages)
    except Exception as exc:
        logger.warning("合规复核 LLM 调用失败，降级放行：%s", exc)
        return GuardrailVerdict(allowed=True, reasons=[])
    verdict_text = (resp.text or "").strip().upper()
    if "UNSAFE" in verdict_text:
        detail = verdict_text.split("UNSAFE", 1)[1].strip(" :：-")
        reason = "LLM复核命中越界内容" + (f"：{detail}" if detail else "")
        return GuardrailVerdict(allowed=False, reasons=[reason])
    return GuardrailVerdict(allowed=True, reasons=[])
