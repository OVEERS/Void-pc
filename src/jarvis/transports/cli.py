from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ..core.app import App
from ..core.dispatcher import ConfirmRequest
from .chat import ChatController

HELP = (
    "Введи help для списка команд и готовых примеров.\n"
    'Например: mkdir "мои файлы" или ls.\n'
    "Если забыть параметры команды, Jarvis покажет что ввести."
)


class CliPort:
    async def send_text(self, text: str) -> None:
        _print_safe(text)

    async def send_file(self, path: Path) -> None:
        _print_safe(f"(файл для отправки: {path})")

    async def ask_confirm(self, req: ConfirmRequest) -> bool:
        answer = await asyncio.to_thread(input, f"Подтвердить?\n{req.summary()}\n[да/нет] > ")
        return answer.strip().lower() in {"да", "д", "y", "yes"}


def _print_safe(text: str) -> None:
    """Выводит текст даже когда кодировка консоли Windows не поддерживает Unicode-символы."""
    stream = sys.stdout
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        safe = text.encode(encoding, errors="replace").decode(encoding)
        print(safe, file=stream)


async def run_cli(app: App) -> None:
    name = app.settings.assistant_name
    chat = ChatController(app, source="cli")
    port = CliPort()
    print(f"{name} (CLI). {HELP}")
    while True:
        try:
            line = (await asyncio.to_thread(input, f"{name}> ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line in {"quit", "exit"}:
            break
        await chat.handle_text("local", line, port)
