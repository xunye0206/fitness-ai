"""训练记录工具：把「用户描述一次运动」转成 TrainingEntry。"""
from app.agent.tool import Tool, ToolResult, ToolContext
from app.modules.training.schemas import TrainingCreate
from app.modules.training.service import record_training


class LogTrainingTool(Tool):
    @property
    def name(self) -> str:
        return "log_training"

    @property
    def description(self) -> str:
        return "当用户描述一次运动/训练时，记录训练条目（如「刚跑了 5 公里」）。"

    def is_write(self) -> bool:
        return True  # 落库 TrainingEntry，属写操作（受护栏单轮回写上限约束）

    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "exercise_type": {"type": "string", "description": "运动类型，如 跑步/撸铁/游泳"},
                "duration_min": {"type": "integer", "description": "时长（分钟）"},
                "intensity": {
                    "type": "string",
                    "description": "强度",
                    "enum": ["low", "medium", "high"],
                },
                "calories_burned": {"type": "number", "description": "消耗热量(kcal)，模型估算或用户给出，默认0"},
                "notes": {"type": "string", "description": "备注（可选）"},
                "date": {"type": "string", "description": "日期 YYYY-MM-DD（可选，默认今天）"},
            },
            "required": ["exercise_type", "duration_min", "intensity"],
        }

    async def run(self, arguments: dict, ctx: ToolContext) -> ToolResult:
        payload = TrainingCreate(
            date=arguments.get("date"),
            exercise_type=str(arguments.get("exercise_type", "")).strip() or "训练",
            duration_min=int(arguments.get("duration_min") or 0),
            intensity=str(arguments.get("intensity") or "medium"),
            calories_burned=float(arguments.get("calories_burned") or 0.0),
            notes=str(arguments.get("notes") or ""),
        )
        entry = await record_training(ctx.session, ctx.user_id, payload)
        return ToolResult(
            content=(
                f"已记录训练：{entry.exercise_type} {entry.duration_min}分钟"
                f"（{entry.intensity}强度，约 {entry.calories_burned:.0f} kcal）"
            )
        )
