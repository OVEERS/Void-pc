from __future__ import annotations

from ..core.tool import Risk, ToolContext, ToolError, ToolResult, tool
from ..core.undo import run_undo


@tool("ping", "Проверка связи: ассистент отвечает, что он жив.")
def ping(ctx: ToolContext) -> str:
    return f"{ctx.settings.assistant_name} на связи."


@tool("tools_help", "Показать список доступных функций с описанием и уровнем риска.")
def tools_help(ctx: ToolContext) -> ToolResult:
    icons = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    lines = [f"{icons[t.risk.value]} {t.name}: {t.description}" for t in ctx.registry.all()]
    return ToolResult(True, "\n".join(lines), data=ctx.registry.describe())


@tool("undo_last", "Откатить последнее обратимое действие (создание, запись, удаление).", risk=Risk.YELLOW)
def undo_last(ctx: ToolContext) -> ToolResult:
    row = ctx.db.last_undoable()
    if row is None:
        raise ToolError("Нечего откатывать")
    import json

    done = run_undo(ctx.sandbox, ctx.trash, json.loads(row["undo"]))
    ctx.db.mark_undone(row["id"])
    return ToolResult(True, f"Откатил действие #{row['id']} ({row['tool']}): " + "; ".join(done))
