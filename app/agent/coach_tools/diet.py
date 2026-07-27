"""饮食记录（文字描述）工具：用 LLM 从文字估算营养并落库为 DietEntry。

视觉识别（图片→饮食）走 graph.run_diet_recognition，不在这里；这里只处理
「用户用文字说吃了什么」的情况，复用 graph.parse_estimate 的 JSON 解析能力。
"""
from app.agent.tool import Tool, ToolResult, ToolContext
from app.agent.graph import parse_estimate
from app.agent.schemas import FoodEstimate
from app.llm.base import Message
from app.llm.router import reason
from app.modules.diet.domain import DietEntry
from app.modules.diet.service import infer_meal_type

DIET_TEXT_PROMPT = (
    "请根据用户描述的食物，估算其热量(kcal)与宏量营养素"
    "(蛋白质/碳水/脂肪, 单位g)、置信度(0-1)，并给一句说明。"
    "只返回一个 JSON 对象，不要任何额外文字，字段如下："
    '{"name":"食物名","calories":0,"protein_g":0,"carbs_g":0,"fat_g":0,"confidence":0,"note":"一句话说明"}'
)


class LogDietFromTextTool(Tool):
    @property
    def name(self) -> str:
        return "log_diet_from_text"

    @property
    def description(self) -> str:
        return "当用户用文字描述自己吃了什么（如「刚吃了两个鸡腿和一碗米饭」）时，记录一条饮食。模型需从描述中估算食物名与营养。"

    def is_write(self) -> bool:
        return True  # 落库 DietEntry，属写操作（受护栏单轮回写上限约束）

    def parameters_schema(self) -> dict:
        return {
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
        }

    async def run(self, arguments: dict, ctx: ToolContext) -> ToolResult:
        description = str(arguments.get("description", "")).strip()
        if not description:
            return ToolResult(content="未提供食物描述，未记录。", is_error=True)
        result = await reason([
            Message(role="system", content=DIET_TEXT_PROMPT),
            Message(role="user", content=description),
        ])
        est: FoodEstimate | None = parse_estimate(result) if result.ok else None
        if est is None:
            est = FoodEstimate(
                name=description[:40],
                calories=0.0,
                protein_g=0.0,
                carbs_g=0.0,
                fat_g=0.0,
                confidence=0.0,
                note="估算失败，默认记录描述",
            )
        entry = DietEntry(
            user_id=ctx.user_id,
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
        ctx.session.add(entry)
        await ctx.session.commit()
        await ctx.session.refresh(entry)
        return ToolResult(
            content=(
                f"已记录饮食：{est.name}（约 {est.calories:.0f} kcal，"
                f"蛋白 {est.protein_g:.0f}g / 碳水 {est.carbs_g:.0f}g / 脂肪 {est.fat_g:.0f}g）"
            )
        )
