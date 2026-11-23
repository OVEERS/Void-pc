from __future__ import annotations

import importlib
import logging
import pkgutil

from .tool import Tool

log = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self.failed_modules: dict[str, str] = {}

    def register(self, t: Tool) -> None:
        existing = self._tools.get(t.name)
        if existing is not None:
            if existing is t:
                return
            raise ValueError(f"Дубликат инструмента: {t.name}")
        self._tools[t.name] = t

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return sorted(self._tools.values(), key=lambda t: t.name)

    def describe(self) -> list[dict]:
        """Компактное описание для LLM-роутера (этап 6)."""
        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters, "risk": t.risk.value}
            for t in self.all()
        ]

    def discover(self, package: str = "jarvis.tools") -> int:
        """Импортирует все модули пакета и регистрирует найденные @tool.

        Сломанный плагин не должен ронять весь Jarvis: ошибка пишется в лог и в failed_modules.
        """
        pkg = importlib.import_module(package)
        for mod_info in pkgutil.walk_packages(pkg.__path__, package + "."):
            if mod_info.name.rsplit(".", 1)[-1].startswith("_"):
                continue
            try:
                module = importlib.import_module(mod_info.name)
                for obj in vars(module).values():
                    t = getattr(obj, "__tool__", None)
                    if isinstance(t, Tool):
                        self.register(t)
            except Exception as e:  # noqa: BLE001: плагин может сломаться чем угодно
                self.failed_modules[mod_info.name] = f"{type(e).__name__}: {e}"
                log.exception("Не удалось загрузить плагин %s", mod_info.name)
        return len(self._tools)
