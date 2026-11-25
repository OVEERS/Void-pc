from __future__ import annotations

import inspect
import json
from typing import TYPE_CHECKING, Any

from .tool import Tool

if TYPE_CHECKING:
    from .registry import ToolRegistry


ALIASES = {
    "start": "tools_help",
    "help": "tools_help",
    "tools": "tools_help",
    "undo": "undo_last",
    "ls": "fs_list",
    "dir": "fs_list",
    "tree": "fs_tree",
    "info": "fs_info",
    "find": "fs_search",
    "search": "fs_search",
    "mkdir": "fs_mkdir",
    "touch": "fs_create_file",
    "newfile": "fs_create_file",
    "write": "fs_write_text",
    "copy": "fs_copy",
    "move": "fs_move",
    "rename": "fs_rename",
    "cd": "fs_cd",
    "pwd": "fs_pwd",
    "delete": "fs_delete",
    "del": "fs_delete",
    "clipboard": "fs_clipboard",
    "paste": "fs_paste",
    "getfile": "fs_get_file",
    "preview_write": "fs_preview_write",
    "replace": "fs_preview_replace",
    "append": "fs_preview_append",
    "apply_edit": "fs_apply_edit",
}

_EXAMPLES: dict[str, dict[str, Any]] = {
    "fs_list": {"path": "."},
    "fs_tree": {"path": "."},
    "fs_info": {"path": "заметки.txt"},
    "fs_search": {"path": ".", "pattern": "*.txt"},
    "fs_mkdir": {"path": "папка/новая"},
    "fs_create_file": {"path": "заметки.md", "template": "markdown"},
    "fs_write_text": {"path": "заметки.txt", "content": "Привет!"},
    "fs_copy": {"source": "заметки.txt", "destination": "копия.txt"},
    "fs_move": {"source": "заметки.txt", "destination": "архив/"},
    "fs_rename": {"path": "заметки.txt", "new_name": "план.txt"},
    "fs_clipboard": {"path": "заметки.txt", "mode": "copy"},
    "fs_paste": {"destination": "."},
    "fs_cd": {"path": "папка"},
    "fs_preview_write": {"path": "заметки.txt", "content": "Новый текст"},
    "fs_preview_replace": {
        "path": "заметки.txt", "old_text": "старый текст", "new_text": "новый текст",
    },
    "fs_preview_append": {"path": "заметки.txt", "content": "\nДополнение"},
    "fs_apply_edit": {"token": "<токен из предпросмотра>"},
    "fs_delete": {"path": "файл_или_папка"},
    "fs_get_file": {"path": "файл.txt"},
}

_TYPE_NAMES = {
    "string": "текст",
    "integer": "целое число",
    "number": "число",
    "boolean": "да/нет",
}
_SAMPLE_VALUES = {
    "path": "путь/к/файлу",
    "source": "исходный_файл.txt",
    "destination": "папка_или_новый_путь",
    "new_name": "новое_имя.txt",
    "pattern": "*.txt",
    "old_text": "старый текст",
    "new_text": "новый текст",
    "content": "пример текста",
    "token": "<токен из предпросмотра>",
}


def _fallback_value(name: str, schema: dict) -> Any:
    if "enum" in schema:
        return "cut" if name == "mode" and "cut" in schema["enum"] else schema["enum"][0]
    kind = schema.get("type")
    if kind == "string":
        return _SAMPLE_VALUES.get(name, "пример")
    if kind == "integer":
        return schema.get("minimum", 1)
    if kind == "number":
        return schema.get("minimum", 1)
    if kind == "boolean":
        return False
    return None


def _parameter_details(name: str, schema: dict, default: Any) -> str:
    bits = [_TYPE_NAMES.get(schema.get("type"), schema.get("type", "значение"))]
    if "enum" in schema:
        bits.append("варианты: " + ", ".join(map(str, schema["enum"])))
    bounds = []
    if "minimum" in schema:
        bounds.append(f"от {schema['minimum']}")
    if "maximum" in schema:
        bounds.append(f"до {schema['maximum']}")
    if bounds:
        bits.append(", ".join(bounds))
    if schema.get("description"):
        bits.append(schema["description"])
    if default is not inspect.Parameter.empty:
        bits.append("по умолчанию: " + json.dumps(default, ensure_ascii=False))
    return "; ".join(bits)


def format_tool_usage(tool: Tool) -> str:
    icons = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    lines = [f"{icons[tool.risk.value]} {tool.name} — {tool.description}"]
    properties = tool.parameters.get("properties", {})
    required = set(tool.parameters.get("required", []))
    if properties:
        try:
            signature = inspect.signature(tool.handler)
        except (TypeError, ValueError):
            signature = None
        for name, schema in properties.items():
            parameter = signature.parameters.get(name) if signature else None
            default = parameter.default if parameter else inspect.Parameter.empty
            label = "нужно" if name in required else "необязательно"
            lines.append(f"  {label}: {name} ({_parameter_details(name, schema, default)})")
    else:
        lines.append("  Параметры не нужны.")

    sample = dict(_EXAMPLES.get(tool.name, {}))
    for name in required:
        if name not in sample:
            sample[name] = _fallback_value(name, properties.get(name, {}))
    if sample:
        command = f"{tool.name} {json.dumps(sample, ensure_ascii=False)}"
        lines.append(f"  Пример: {command}")
    elif not required:
        lines.append(f"  Пример: {tool.name}")

    aliases = sorted(alias for alias, target in ALIASES.items() if target == tool.name and alias not in {
        "start", "help", "tools",
    })
    if aliases:
        lines.append(f"  Короткая команда: {aliases[0]}")
    return "\n".join(lines)


def format_tools_help(registry: "ToolRegistry") -> str:
    lines = [
        "КАК ПОЛЬЗОВАТЬСЯ",
        "Введи команду и нажми Enter. Самый простой формат — команда и путь: mkdir \"моя папка\".",
        "Можно и явно: fs_mkdir {\"path\": \"моя папка\"}. Пути с пробелами заключай в кавычки.",
        "Если забыть обязательные параметры, Jarvis покажет пример. help можно вызвать в любой момент.",
        "",
        "БЫСТРЫЕ КОМАНДЫ",
        "  ls [папка]       показать файлы (без пути — текущая папка)",
        "  mkdir <путь>     создать папку",
        "  cd <папка>       перейти в папку; pwd — показать текущую",
        "  touch <путь>     создать пустой файл",
        "  find             поиск; команда покажет нужные параметры",
        "  delete <путь>    отправить в корзину с подтверждением",
        "  undo             отменить последнее обратимое действие",
        "  help             эта справка",
        "",
        "ПОЛНЫЙ СПИСОК КОМАНД",
    ]
    lines.extend(format_tool_usage(tool) for tool in registry.all())
    lines.extend([
        "",
        "🟢 чтение, 🟡 изменение с записью в журнал, 🔴 требуется подтверждение.",
        "Для выхода из CLI введи quit или exit.",
    ])
    return "\n".join(lines)
