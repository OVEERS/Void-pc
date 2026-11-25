"""Ядро чата: одна и та же логика для консоли, Telegram и (позже) Max."""
from __future__ import annotations

import logging
import os
import tempfile
import time
from io import BufferedWriter, FileIO
from pathlib import Path
from typing import Awaitable, Callable

from ..core.app import App
from ..core.help import format_tool_usage
from ..core.sandbox import SandboxError
from .base import ChatPort
from .common import (CommandError, format_history, is_blocked_extension, parse_command, safe_filename,
                     unique_path)

log = logging.getLogger(__name__)

MAX_INCOMING_BYTES = 20 * 1024 * 1024  # лимит Bot API на скачивание файла ботом
INBOX_DIR = "inbox"


def _incoming_limit_label() -> str:
    megabyte = 1024 * 1024
    if MAX_INCOMING_BYTES % megabyte == 0:
        return f"{MAX_INCOMING_BYTES // megabyte} МБ"
    return f"{MAX_INCOMING_BYTES} Б"


class IncomingFileTooLarge(Exception):
    """The incoming file exceeded the download limit."""


class LimitedDownloadWriter(BufferedWriter):
    def __init__(self, raw: FileIO, limit: int):
        super().__init__(raw)
        self.limit = limit
        self.written = 0

    def write(self, data: bytes) -> int:
        if self.written + len(data) > self.limit:
            raise IncomingFileTooLarge
        count = super().write(data)
        self.written += count
        return count


def _cleanup_part(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.exception("Не удалось удалить временный файл приёма %s", path)


class ChatController:
    def __init__(self, app: App, source: str):
        self.app = app
        self.source = source

    async def _say(self, port: ChatPort, text: str, user_id: str | None = None) -> None:
        text = text or "(пусто)"
        self.app.db.add_history("assistant", self.source, text, user_id)
        await port.send_text(text)

    async def handle_text(self, user_id: str, text: str, port: ChatPort) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.app.db.add_history("user", self.source, text, user_id)
        try:
            name, params = parse_command(text, self.app.registry)
        except CommandError as e:
            await self._say(port, f"❌ {e}\nНапиши help, чтобы увидеть примеры команд.", user_id)
            return

        if name == "history":
            await self._say(port, format_history(self.app.db, source=self.source, user_id=user_id), user_id)
            return

        selected_tool = self.app.registry.get(name)
        if selected_tool is not None and not params:
            required = selected_tool.parameters.get("required", [])
            if required:
                await self._say(port, format_tool_usage(selected_tool), user_id)
                return

        res = await self.app.dispatcher.call(
            name, params, source=self.source, user_id=user_id, confirm=port.ask_confirm
        )
        response = ("✅ " if res.ok else "❌ ") + res.message
        if res.status == "invalid" and selected_tool is not None:
            response += "\n\n" + format_tool_usage(selected_tool)
        elif res.status == "unknown_tool":
            response += "\nНапиши help, чтобы увидеть доступные команды."
        await self._say(port, response, user_id)

        attach = res.data.get("attach") if res.ok and isinstance(res.data, dict) else None
        if attach:
            try:
                await port.send_file(Path(attach))
            except Exception:  # noqa: BLE001
                log.exception("Не удалось отправить файл %s", attach)
                await self._say(port, "❌ Не удалось отправить файл", user_id)

    async def handle_incoming_file(
        self,
        user_id: str,
        filename: str,
        size: int | None,
        download: Callable[[LimitedDownloadWriter], Awaitable[None]],
        port: ChatPort,
    ) -> Path | None:
        """Сохраняет присланный файл в <рабочая папка>/inbox. Действие попадает в журнал и откатывается undo_last."""
        clean = safe_filename(filename)
        if is_blocked_extension(clean):
            await self._say(port, "❌ Исполняемые файлы и скрипты не принимаю", user_id)
            return None
        if size is not None and size > MAX_INCOMING_BYTES:
            await self._say(
                port, f"❌ Файл больше {_incoming_limit_label()}, Telegram не даёт боту скачать такой", user_id
            )
            return None

        try:
            inbox = self.app.sandbox.resolve(INBOX_DIR)
            inbox.mkdir(exist_ok=True)
        except (SandboxError, OSError) as e:
            await self._say(port, f"❌ Не могу сохранить: {e}", user_id)
            return None

        aid = self.app.db.start_action(
            "receive_file", "yellow", self.source, user_id, {"name": clean, "size": size}
        )
        t0 = time.monotonic()
        part: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(prefix=".jarvis-", suffix=".part", dir=inbox)
            part = Path(temp_name)
            raw = os.fdopen(descriptor, "wb", buffering=0)
            with LimitedDownloadWriter(raw, MAX_INCOMING_BYTES) as destination:
                await download(destination)
            if part.stat().st_size > MAX_INCOMING_BYTES:
                raise IncomingFileTooLarge

            target = None
            for _ in range(1000):
                candidate = self.app.sandbox.resolve(
                    str(unique_path(inbox, clean)), allow_root=False
                )
                try:
                    os.link(part, candidate)
                except FileExistsError:
                    continue
                target = candidate
                break
            if target is None:
                raise FileExistsError("Не удалось подобрать свободное имя файла")
        except IncomingFileTooLarge:
            _cleanup_part(part)
            elapsed = int((time.monotonic() - t0) * 1000)
            limit_label = _incoming_limit_label()
            self.app.db.finish_action(aid, "failed", f"Превышен лимит {limit_label}", elapsed)
            await self._say(port, f"❌ Файл больше {limit_label}, скачивание остановлено", user_id)
            return None
        except Exception as e:  # noqa: BLE001
            log.exception("Ошибка приёма файла")
            _cleanup_part(part)
            self.app.db.finish_action(aid, "error", f"{type(e).__name__}", int((time.monotonic() - t0) * 1000))
            await self._say(port, "❌ Не удалось скачать файл", user_id)
            return None

        _cleanup_part(part)
        self.app.db.finish_action(
            aid, "ok", f"Сохранён: {target}", int((time.monotonic() - t0) * 1000),
            {"steps": [{"kind": "remove_created", "path": str(target)}]},
        )
        try:
            shown = target.relative_to(self.app.sandbox.home)
        except ValueError:
            shown = target
        await self._say(port, f"📥 Сохранено: {shown}", user_id)
        return target
