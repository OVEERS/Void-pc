from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol

from ..core.dispatcher import ConfirmRequest


class TransportDisabled(Exception):
    """Транспорт не может работать из-за настроек (плохой токен и т.п.). Перезапускать бесполезно."""


class ChatPort(Protocol):
    """Всё, что ядру чата нужно от конкретного мессенджера.
    Telegram, Max и консоль реализуют один и тот же набор из трёх методов."""

    async def send_text(self, text: str) -> None: ...

    async def send_file(self, path: Path) -> None: ...

    async def ask_confirm(self, req: ConfirmRequest) -> bool: ...


class Transport(ABC):
    name: str = "transport"

    @abstractmethod
    async def run(self) -> None:
        """Работает, пока не отменят задачу (CancelledError). Ресурсы освобождает в finally."""
