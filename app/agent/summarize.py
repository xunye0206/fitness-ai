"""会话压缩：把超预算的历史压成一条摘要（对应 OpenCode 的 Summarize）。

仅在 SessionManager 判定超预算、且注入了本函数时才调用——
所以**正常对话不烧额外 LLM**，只有历史真的太长才会触发一次压缩。
压缩失败有兜底（取最近几条拼接），不阻断主链路。
"""
from typing import List

from app.agent.session import StoredMessage
from app.llm.base import Message
from app.llm.router import reason

SUMMARY_PROMPT = (
    "请把以下健身教练对话历史压缩成一段简洁摘要，保留：用户目标与偏好、"
    "已记录的关键饮食/训练、未完成的请求、重要上下文。不要编造新信息。"
    "只输出摘要本身。"
)


async def summarize_history(messages: List[StoredMessage]) -> str:
    """把 StoredMessage 列表压成一条摘要字符串。"""
    convo = "\n".join(f"[{m.role}] {m.content}" for m in messages)
    try:
        res = await reason([
            Message(role="system", content=SUMMARY_PROMPT),
            Message(role="user", content=convo),
        ])
        if res.ok and res.text.strip():
            return res.text.strip()
    except Exception as exc:  # 降级：绝不因压缩失败阻断对话
        logger = __import__("logging").getLogger("fitness_agent.session")
        logger.warning("会话压缩失败，降级为最近拼接：%s", exc)
    # 兜底：取最近 4 条拼接
    return "\n".join(f"[{m.role}] {m.content}" for m in messages[-4:])
