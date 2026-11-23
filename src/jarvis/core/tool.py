from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

from jsonschema import Draft202012Validator

if TYPE_CHECKING:  # только для подсказок типов, без циклических импортов
    from .config import Settings
    from .db import Database
    from .registry import ToolRegistry
    from .sandbox import PathSandbox
    from .trash import Trash

# Имена в стиле function calling у LLM: латиница, цифры, подчёркивание
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class Risk(str, Enum):
    GREEN = "green"    # только чтение: выполняется сразу
    YELLOW = "yellow"  # изменяет данные: выполняется и пишется в журнал (с откатом, где возможно)
    RED = "red"        # опасное: только с подтверждением


class ToolError(Exception):
    """Ожидаемая ошибка инструмента. Текст показывается пользователю как есть."""


@dataclass
class ToolResult:
    ok: bool
    message: str
    data: Any = None
    undo: dict | None = None  # {"steps": [...]} для отката


@dataclass
class ToolContext:
    settings: "Settings"
    db: "Database"
    sandbox: "PathSandbox"
    trash: "Trash"
    registry: "ToolRegistry"
    source: str
    user_id: str | None
    action_id: int


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    risk: Risk
    handler: Callable[..., Any]
    timeout_s: float | None = None


def tool(
    name: str,
    description: str,
    parameters: dict | None = None,
    risk: Risk = Risk.GREEN,
    timeout_s: float | None = None,
):
    """Декоратор: превращает функцию `f(ctx, **params)` в инструмент.

    Функция возвращает ToolResult или просто строку. Может быть `def` или `async def`.
    """
    if not NAME_RE.match(name):
        raise ValueError(f"Некорректное имя инструмента: {name!r}")

    schema = dict(parameters or {})
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    schema.setdefault("additionalProperties", False)
    if schema["type"] != "object":
        raise ValueError(f"{name}: схема параметров должна быть объектом")
    Draft202012Validator.check_schema(schema)

    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn.__tool__ = Tool(name, description, schema, risk, fn, timeout_s)  # type: ignore[attr-defined]
        return fn

    return wrap
