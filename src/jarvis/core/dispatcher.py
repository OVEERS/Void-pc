from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator

from .config import Settings
from .db import Database
from .registry import ToolRegistry
from .sandbox import PathSandbox, SandboxError
from .tool import Risk, Tool, ToolContext, ToolError, ToolResult
from .trash import Trash

log = logging.getLogger(__name__)


@dataclass
class ConfirmRequest:
    tool: str
    risk: Risk
    description: str
    params: dict

    def summary(self) -> str:
        p = json.dumps(self.params, ensure_ascii=False)
        return f"{self.description}\nПараметры: {p[:300]}"


Confirmer = Callable[[ConfirmRequest], Awaitable[bool]]


@dataclass
class DispatchResult:
    ok: bool
    status: str  # ok | failed | invalid | unknown_tool | denied | timeout | error
    message: str
    data: Any = None
    action_id: int | None = None


def validate_params(t: Tool, params: dict) -> list[str]:
    validator = Draft202012Validator(t.parameters)
    errors = sorted(validator.iter_errors(params), key=lambda e: list(map(str, e.path)))
    return [f"{'.'.join(map(str, e.path)) or 'параметры'}: {e.message}" for e in errors]


class Dispatcher:
    """Единая дверь для любых команд: CLI, Telegram, голос, LLM. Всё идёт через call()."""

    def __init__(self, settings: Settings, registry: ToolRegistry, db: Database,
                 sandbox: PathSandbox, trash: Trash):
        self.settings, self.registry, self.db, self.sandbox, self.trash = settings, registry, db, sandbox, trash

    async def call(self, name: str, params: dict | None = None, *, source: str,
                   user_id: str | None = None, confirm: Confirmer | None = None) -> DispatchResult:
        params = params or {}
        t = self.registry.get(name)
        if t is None:
            aid = self.db.start_action(name, "unknown", source, user_id, params)
            self.db.finish_action(aid, "unknown_tool", "нет такой функции", 0)
            return DispatchResult(False, "unknown_tool", f"Нет такой функции: {name}", action_id=aid)

        aid = self.db.start_action(name, t.risk.value, source, user_id, params)

        errors = validate_params(t, params)
        if errors:
            msg = "Неверные параметры: " + "; ".join(errors)
            self.db.finish_action(aid, "invalid", msg, 0)
            return DispatchResult(False, "invalid", msg, action_id=aid)

        if t.risk is Risk.RED:
            allowed = False
            if confirm is not None:
                req = ConfirmRequest(t.name, t.risk, t.description, params)
                try:
                    allowed = await asyncio.wait_for(confirm(req), self.settings.confirm_timeout_s)
                except TimeoutError:
                    allowed = False
            if not allowed:
                msg = "Действие не подтверждено, отменено"
                self.db.finish_action(aid, "denied", msg, 0)
                return DispatchResult(False, "denied", msg, action_id=aid)

        ctx = ToolContext(self.settings, self.db, self.sandbox, self.trash, self.registry,
                          source, user_id, aid)
        t0 = time.monotonic()
        try:
            res = await self._run(t, ctx, params)
        except TimeoutError:
            ms = int((time.monotonic() - t0) * 1000)
            msg = "Превышено время выполнения"
            log.warning("Таймаут инструмента %s (поток может доработать в фоне)", name)
            self.db.finish_action(aid, "timeout", msg, ms)
            return DispatchResult(False, "timeout", msg, action_id=aid)
        except (SandboxError, ToolError) as e:
            ms = int((time.monotonic() - t0) * 1000)
            status = "denied" if isinstance(e, SandboxError) else "failed"
            self.db.finish_action(aid, status, str(e), ms)
            return DispatchResult(False, status, str(e), action_id=aid)
        except Exception as e:  # noqa: BLE001
            ms = int((time.monotonic() - t0) * 1000)
            log.exception("Ошибка инструмента %s", name)
            self.db.finish_action(aid, "error", f"{type(e).__name__}: {e}", ms)
            return DispatchResult(False, "error", f"Внутренняя ошибка ({type(e).__name__}), подробности в логе",
                                  action_id=aid)

        ms = int((time.monotonic() - t0) * 1000)
        status = "ok" if res.ok else "failed"
        self.db.finish_action(aid, status, res.message, ms, res.undo if res.ok else None)
        return DispatchResult(res.ok, status, res.message, res.data, aid)

    async def _run(self, t: Tool, ctx: ToolContext, params: dict) -> ToolResult:
        timeout = t.timeout_s or self.settings.tool_timeout_s
        if inspect.iscoroutinefunction(t.handler):
            coro = t.handler(ctx, **params)
        else:
            coro = asyncio.to_thread(t.handler, ctx, **params)
        raw = await asyncio.wait_for(coro, timeout)
        if isinstance(raw, ToolResult):
            return raw
        if isinstance(raw, str):
            return ToolResult(True, raw)
        raise TypeError(f"{t.name}: инструмент вернул {type(raw).__name__}, ожидался ToolResult или str")
