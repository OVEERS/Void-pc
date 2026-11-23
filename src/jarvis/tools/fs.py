from __future__ import annotations

from ..core.tool import Risk, ToolContext, ToolError, ToolResult, tool

MAX_WRITE_BYTES = 1_000_000


@tool(
    "fs_list",
    "Показать содержимое папки.",
    {"properties": {"path": {"type": "string"}, "max_items": {"type": "integer", "minimum": 1, "maximum": 1000}},
     "required": ["path"]},
)
def fs_list(ctx: ToolContext, path: str, max_items: int = 200) -> ToolResult:
    p = ctx.sandbox.resolve(path)
    if not p.is_dir():
        raise ToolError(f"Это не папка: {p}")
    entries = []
    for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if len(entries) >= max_items:
            break
        try:
            size = child.stat().st_size if child.is_file() else None
        except OSError:
            size = None
        entries.append({"name": child.name, "dir": child.is_dir(), "size": size})
    lines = [("📁 " if e["dir"] else "📄 ") + e["name"] + (f"  ({e['size']} Б)" if e["size"] is not None else "")
             for e in entries]
    return ToolResult(True, "\n".join(lines) or "(пусто)", data=entries)


@tool(
    "fs_mkdir",
    "Создать папку (вместе с недостающими родительскими).",
    {"properties": {"path": {"type": "string"}}, "required": ["path"]},
    risk=Risk.YELLOW,
)
def fs_mkdir(ctx: ToolContext, path: str) -> ToolResult:
    p = ctx.sandbox.resolve(path)
    if p.exists():
        if p.is_dir():
            return ToolResult(True, f"Папка уже есть: {p}")
        raise ToolError(f"По этому пути лежит файл: {p}")
    # самый верхний ещё не существующий предок: его и откатываем
    top = p
    while not top.parent.exists():
        top = top.parent
    p.mkdir(parents=True)
    return ToolResult(True, f"Создана папка: {p}", undo={"steps": [{"kind": "remove_created", "path": str(top)}]})


@tool(
    "fs_write_text",
    "Создать текстовый файл (или перезаписать с overwrite=true; старая версия уходит в корзину).",
    {"properties": {"path": {"type": "string"}, "content": {"type": "string"}, "overwrite": {"type": "boolean"}},
     "required": ["path", "content"]},
    risk=Risk.YELLOW,
)
def fs_write_text(ctx: ToolContext, path: str, content: str, overwrite: bool = False) -> ToolResult:
    p = ctx.sandbox.resolve(path, allow_root=False)
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise ToolError("Слишком большой файл для этой функции (лимит 1 МБ)")
    if not p.parent.is_dir():
        raise ToolError(f"Папки не существует: {p.parent}. Сначала создай её (fs_mkdir).")

    steps: list[dict] = [{"kind": "remove_created", "path": str(p)}]
    if p.exists():
        if p.is_dir():
            raise ToolError(f"По этому пути папка: {p}")
        if not overwrite:
            raise ToolError(f"Файл уже существует: {p}. Для замены нужен overwrite=true.")
        trash_id = ctx.trash.put(p, ctx.action_id)
        steps.append({"kind": "restore_trash", "trash_id": trash_id})
    p.write_bytes(data)
    return ToolResult(True, f"Записано {len(data)} Б в {p}", undo={"steps": steps})


@tool(
    "fs_delete",
    "Удалить файл или папку (перемещение в корзину Jarvis, можно откатить).",
    {"properties": {"path": {"type": "string"}}, "required": ["path"]},
    risk=Risk.RED,
)
def fs_delete(ctx: ToolContext, path: str) -> ToolResult:
    p = ctx.sandbox.resolve(path, allow_root=False)
    if not p.exists():
        raise ToolError(f"Не найдено: {p}")
    trash_id = ctx.trash.put(p, ctx.action_id)
    return ToolResult(True, f"В корзине: {p.name}", undo={"steps": [{"kind": "restore_trash", "trash_id": trash_id}]})
