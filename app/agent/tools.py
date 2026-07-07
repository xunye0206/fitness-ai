"""agent 可调用的「工具」：把对话意图映射到真实业务动作。

设计目标（对应项目约定：可看懂、可改、不写聪明但说不清的写法）：
- TOOLS 是标准的 OpenAI function-calling schema，交给 DeepSeek 决定何时调用。
- execute_tool 只做一件事：根据工具名分发到对应模块的 service 函数，返回人类可读结果。
- 所有落库复用已有 service（diet/training/report），不重复写存储逻辑。
"""
from datetime import datetime, timezone
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.agent.graph import parse_estimate
from app.agent.schemas import FoodEstimate
from app.llm.base import Message
from app.llm.router import reason
from app.modules.diet.domain import DietEntry
from app.modules.diet.service import infer_meal_type
from app.modules.report.service import generate_report
from app.modules.training.schemas import TrainingCreate
from app.modules.training.service import record_training

# 文本描述 → 营养估算的提示词（复用 graph.parse_estimate 的 JSON 解析）
DIET_TEXT_PROMPT = (
    "请根据用户描述的食物，估算其热量(kcal)与宏量营养素"
    "(蛋白质/碳水/脂肪, 单位g)、置信度(0-1)，并给一句说明。"
    "只返回一个 JSON 对象，不要任何额外文字，字段如下："
    '{"name":"食物名","calories":0,"protein_g":0,"carbs_g":0,"fat_g":0,"confidence":0,"note":"一句话说明"}'
)


# ---- 工具清单（OpenAI function-calling 格式）----
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "log_diet_from_text",
            "description": "当用户用文字描述自己吃了什么（如「刚吃了两个鸡腿和一碗米饭」）时，"
                           "记录一条饮食。模型需从描述中估算食物名与营养。",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "用户描述的食物内容，例如「午餐：牛肉面一碗+可乐一瓶」",
                    },
                    "meal_type": {
                        "type": "string",
                        "description": "餐别（可选）：breakfast/lunch/dinner/snack",
                        "enum": ["breakfast", "lunch", "dinner", "snack"],
                    },
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_training",
            "description": "当用户描述一次运动/训练时，记录训练条目。",
            "parameters": {
                "type": "object",
                "properties": {
                    "exercise_type": {"type": "string", "description": "运动类型，如 跑步/撸铁/游泳"},
                    "duration_min": {"type": "integer", "description": "时长（分钟）"},
                    "intensity": {
                        "type": "string",
                        "description": "强度",
                        "enum": ["low", "medium", "high"],
                    },
                    "calories_burned": {
                        "type": "number",
                        "description": "消耗热量(kcal)，模型估算或用户给出，默认0",
                    },
                    "notes": {"type": "string", "description": "备注（可选）"},
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD（可选，默认今天）"},
                },
                "required": ["exercise_type", "duration_min", "intensity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": "当用户要求生成日报/周报/总结（如「帮我生成今天的报告」）时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "回顾天数，默认7（即近7天日报）",
                    },
                },
                "required": [],
            },
        },
    },
]


async def execute_tool(
    name: str, arguments: dict, session: AsyncSession, user_id: int
) -> str:
    """执行一个工具调用，返回人类可读的结果字符串（回填给模型作 tool 消息）。"""
    try:
        if name == "log_diet_from_text":
            return await _tool_log_diet(arguments, session, user_id)
        if name == "log_training":
            return await _tool_log_training(arguments, session, user_id)
        if name == "generate_report":
            return await _tool_generate_report(arguments, session, user_id)
        return f"未知工具：{name}"
    except Exception as exc:
        return f"工具 {name} 执行失败：{exc}"


async def _tool_log_diet(args: dict, session: AsyncSession, user_id: int) -> str:
    description = str(args.get("description", "")).strip()
    if not description:
        return "未提供食物描述，未记录。"
    # 用文本推理估算营养（复用 diet 的 JSON 解析器）
    result = await reason([
        Message(role="system", content=DIET_TEXT_PROMPT),
        Message(role="user", content=description),
    ])
    est: FoodEstimate | None = parse_estimate(result) if result.ok else None
    if est is None:
        est = FoodEstimate(
            name=description[:40], calories=0.0, protein_g=0.0,
            carbs_g=0.0, fat_g=0.0, confidence=0.0, note="估算失败，默认记录描述",
        )
    entry = DietEntry(
        user_id=user_id,
        image_path=None,
        name=est.name,
        calories=est.calories,
        protein_g=est.protein_g,
        carbs_g=est.carbs_g,
        fat_g=est.fat_g,
        confidence=est.confidence,
        status="confirmed",
        raw_estimate=est.note,
    )
    entry.meal_type = infer_meal_type(entry.created_at)
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return (
        f"已记录饮食：{est.name}（约 {est.calories:.0f} kcal，"
        f"蛋白 {est.protein_g:.0f}g / 碳水 {est.carbs_g:.0f}g / 脂肪 {est.fat_g:.0f}g）"
    )


async def _tool_log_training(args: dict, session: AsyncSession, user_id: int) -> str:
    payload = TrainingCreate(
        date=args.get("date"),
        exercise_type=str(args.get("exercise_type", "")).strip() or "训练",
        duration_min=int(args.get("duration_min") or 0),
        intensity=str(args.get("intensity") or "medium"),
        calories_burned=float(args.get("calories_burned") or 0.0),
        notes=str(args.get("notes") or ""),
    )
    entry = await record_training(session, user_id, payload)
    return f"已记录训练：{entry.exercise_type} {entry.duration_min}分钟（{entry.intensity}强度，约 {entry.calories_burned:.0f} kcal）"


async def _tool_generate_report(args: dict, session: AsyncSession, user_id: int) -> str:
    days = int(args.get("days") or 7)
    report = await generate_report(session, user_id, days=days)
    return f"已生成{days}天报告。小结：{report.summary}\n建议：{report.advice}"
