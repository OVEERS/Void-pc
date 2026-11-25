from __future__ import annotations

from ..core.help import format_tools_help
from ..core.tool import Risk, ToolContext, ToolError, ToolResult, tool
from ..core.undo import run_undo


@tool("ping", "Проверка связи: ассистент отвечает, что он жив.")
def ping(ctx: ToolContext) -> str:
    return f"{ctx.settings.assistant_name} на связи."


@tool("tools_help", "Показать список доступных функций с описанием и уровнем риска.")
def tools_help(ctx: ToolContext) -> ToolResult:
    return ToolResult(True, format_tools_help(ctx.registry), data=ctx.registry.describe())


@tool("undo_last", "Откатить последнее обратимое действие (создание, запись, удаление).", risk=Risk.YELLOW)
def undo_last(ctx: ToolContext) -> ToolResult:
    row = ctx.db.last_undoable(ctx.source, ctx.user_id)
    if row is None:
        raise ToolError("Нечего откатывать")
    import json

    done = run_undo(ctx.sandbox, ctx.trash, json.loads(row["undo"]))
    ctx.db.mark_undone(row["id"])
    return ToolResult(True, f"Откатил действие #{row['id']} ({row['tool']}): " + "; ".join(done))
