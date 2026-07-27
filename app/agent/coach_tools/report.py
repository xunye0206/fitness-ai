"""报告生成工具：按天数生成训练/饮食回顾报告。"""
from app.agent.tool import Tool, ToolResult, ToolContext
from app.modules.report.service import generate_report


class GenerateReportTool(Tool):
    @property
    def name(self) -> str:
        return "generate_report"

    @property
    def description(self) -> str:
        return "当用户要求生成日报/周报/总结（如「帮我生成今天的报告」）时调用。"

    def is_write(self) -> bool:
        return True  # 落库 DailyReport，属写操作（受护栏单轮回写上限约束）

    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "回顾天数，默认7（即近7天日报）"},
            },
            "required": [],
        }

    async def run(self, arguments: dict, ctx: ToolContext) -> ToolResult:
        days = int(arguments.get("days") or 7)
        report = await generate_report(ctx.session, ctx.user_id, days=days)
        return ToolResult(
            content=f"已生成{days}天报告。小结：{report.summary}\n建议：{report.advice}"
        )
