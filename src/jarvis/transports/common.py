"""Общие утилиты транспортов. Здесь нет зависимостей от aiogram: всё легко тестируется."""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.help import ALIASES
from ..core.sandbox import WIN_RESERVED

if TYPE_CHECKING:
    from ..core.db import Database
    from ..core.registry import ToolRegistry
    from ..core.tool import Tool


class CommandError(Exception):
    """Не удалось разобрать команду. Текст показывается пользователю."""


# Телефонные клавиатуры превращают " в “ ”. Возвращаем обычные кавычки (« » не трогаем: это может быть текст).
_SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‟": '"'})
_KV_RE = re.compile(r"""(\w+)=("[^"]*"|'[^']*'|\S+)""")

BLOCKED_EXT = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".lnk", ".jar", ".dll", ".reg", ".hta", ".cpl",
}


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _coerce(value: str, typ: str | None):
    if typ == "boolean":
        low = value.lower()
        if low in {"true", "yes", "y", "да", "1"}:
            return True
        if low in {"false", "no", "n", "нет", "0"}:
            return False
        raise CommandError(f"Ожидал да/нет, а получил «{value}»")
    if typ in {"integer", "number"}:
        try:
            return int(value) if typ == "integer" else float(value)
        except ValueError:
            raise CommandError(f"Ожидал число, а получил «{value}»") from None
    return value


def _parse_kv(rest: str, tool: "Tool | None") -> dict:
    props = tool.parameters.get("properties", {}) if tool else {}
    params: dict = {}
    pos = 0
    for m in _KV_RE.finditer(rest):
        gap = rest[pos:m.start()].strip()
        if gap:
            raise CommandError(f"Не понял «{gap}». Значения с пробелами бери в кавычки: ключ=\"два слова\"")
        key = m.group(1)
        params[key] = _coerce(_unquote(m.group(2)), props.get(key, {}).get("type"))
        pos = m.end()
    tail = rest[pos:].strip()
    if tail:
        raise CommandError(f"Не понял «{tail}». Значения с пробелами бери в кавычки: ключ=\"два слова\"")
    return params


def parse_command(text: str, registry: "ToolRegistry | None" = None) -> tuple[str, dict]:
    """Разбирает «функция параметры». Три формата параметров:
      1) JSON:              fs_mkdir {"path": "a/b"}
      2) ключ=значение:     fs_write_text path=a.txt content="привет" overwrite=да
      3) одно значение:     fs_list проекты   (для функций с единственным обязательным параметром-строкой)
    """
    t = text.strip()
    if t.startswith("/"):
        t = t[1:]
    parts = t.split(None, 1)
    if not parts:
        raise CommandError("Пустая команда")
    name = parts[0].split("@", 1)[0].lower()  # «/ping@MyBot» -> ping
    name = ALIASES.get(name, name)
    rest = parts[1].strip().translate(_SMART_QUOTES) if len(parts) > 1 else ""
    if not rest:
        return name, {}

    tool = registry.get(name) if registry is not None else None

    if rest.startswith("{"):
        try:
            params = json.loads(rest)
        except json.JSONDecodeError as e:
            raise CommandError(f"Не разобрал JSON: {e.msg} (позиция {e.pos})") from e
        if not isinstance(params, dict):
            raise CommandError("Параметры должны быть JSON-объектом")
        return name, params

    if re.match(r"\w+=", rest):
        return name, _parse_kv(rest, tool)

    if tool is not None:
        required = tool.parameters.get("required", [])
        props = tool.parameters.get("properties", {})
        required_strings = [key for key in required if props.get(key, {}).get("type") == "string"]
        if len(required_strings) == 1:
            return name, {required_strings[0]: _unquote(rest)}
        if not required:
            string_properties = [key for key, schema in props.items() if schema.get("type") == "string"]
            if len(string_properties) == 1:
                return name, {string_properties[0]: _unquote(rest)}

    raise CommandError("Не понял параметры. Пиши JSON {...}, или ключ=значение, "
                       "или просто значение для функции с одним параметром.")


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Режет длинный ответ на части под лимит мессенджера (Telegram: 4096)."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks


def safe_filename(name: str, max_len: int = 120) -> str:
    name = os.path.basename((name or "").replace("\\", "/"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    stem, ext = os.path.splitext(name)
    if stem.upper() in WIN_RESERVED:
        stem = "_" + stem
    if len(stem) + len(ext) > max_len:
        stem = stem[: max_len - len(ext)]
    name = stem + ext
    return name or "file"


def is_blocked_extension(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in BLOCKED_EXT


def unique_path(folder: Path, name: str) -> Path:
    p = folder / name
    if not p.exists() and not p.is_symlink():
        return p
    stem, ext = os.path.splitext(name)
    for i in range(1, 1000):
        c = folder / f"{stem} ({i}){ext}"
        if not c.exists() and not c.is_symlink():
            return c
    raise FileExistsError(name)


def format_history(
    db: "Database", limit: int = 15, *, source: str | None = None, user_id: str | None = None
) -> str:
    rows = db.recent_actions(limit, source=source, user_id=user_id)
    if not rows:
        return "Журнал пуст"
    return "\n".join(
        f"#{r['id']} {r['ts'][11:19]} {r['tool']} [{r['status']}] {(r['result'] or '')[:80]}" for r in rows
    )


class ConfirmBroker:
    """Связывает вопрос «подтвердить?» с нажатием кнопки. Кнопку может нажать только тот, кому задан вопрос."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[int, asyncio.Future[bool]]] = {}

    def create(self, user_id: int) -> tuple[str, "asyncio.Future[bool]"]:
        token = secrets.token_urlsafe(6)
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[token] = (user_id, fut)
        return token, fut

    def resolve(self, token: str, user_id: int, approved: bool) -> bool:
        entry = self._pending.get(token)
        if entry is None or entry[0] != user_id:
            return False
        fut = entry[1]
        if fut.done():
            return False
        fut.set_result(approved)
        return True

    def discard(self, token: str) -> None:
        self._pending.pop(token, None)
