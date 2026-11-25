from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message)

from ..core.app import App
from ..core.dispatcher import ConfirmRequest
from .base import Transport, TransportDisabled
from .chat import ChatController, LimitedDownloadWriter
from .common import ConfirmBroker, split_message

log = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    """Пропускает только владельцев (по числовому ID) и только личные чаты. Остальным не отвечает вообще."""

    def __init__(self, allowed: set[int]):
        self.allowed = allowed
        self._reported: set[int | None] = set()

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        uid = user.id if user else None
        if uid not in self.allowed:
            if uid not in self._reported and len(self._reported) < 100:
                self._reported.add(uid)
                log.warning("Telegram: отклонён доступ, user_id=%s", uid)
            return None
        if isinstance(event, Message) and event.chat.type != "private":
            return None
        return await handler(event, data)


class TelegramPort:
    """Реализация ChatPort для одного входящего сообщения."""

    def __init__(self, transport: "TelegramTransport", message: Message):
        self.t = transport
        self.message = message

    async def send_text(self, text: str) -> None:
        for chunk in split_message(text):
            await self.message.answer(chunk)

    async def send_file(self, path: Path) -> None:
        await self.message.answer_document(FSInputFile(path))

    async def ask_confirm(self, req: ConfirmRequest) -> bool:
        return await self.t.ask_confirm(self.message, req)


class TelegramTransport(Transport):
    name = "telegram"

    def __init__(self, app: App):
        self.app = app
        self.chat = ChatController(app, source="telegram")
        self.broker = ConfirmBroker()
        self.bot: Bot | None = None
        self.allowed = set(app.settings.secrets.telegram_allowed_ids)

    # --- обработчики -----------------------------------------------------
    async def _on_text(self, message: Message) -> None:
        await self.chat.handle_text(str(message.from_user.id), message.text or "", TelegramPort(self, message))

    async def _on_file(self, message: Message) -> None:
        if message.document:
            obj = message.document
            name = obj.file_name or f"file_{obj.file_unique_id}"
        else:
            obj = message.photo[-1]  # самый большой размер
            name = f"photo_{obj.file_unique_id}.jpg"

        async def download(dest: LimitedDownloadWriter) -> None:
            assert self.bot is not None
            await self.bot.download(obj, destination=dest)

        await self.chat.handle_incoming_file(
            str(message.from_user.id), name, obj.file_size, download, TelegramPort(self, message)
        )

    async def _on_voice(self, message: Message) -> None:
        await message.answer("🎙 Голосовые сообщения появятся на этапе 5.")

    async def _on_other(self, message: Message) -> None:
        await message.answer("Не понял. Пришли текстовую команду (help покажет список) или файл.")

    async def _on_callback(self, cb: CallbackQuery) -> None:
        parts = (cb.data or "").split(":")
        if len(parts) != 3 or parts[0] != "c":
            await cb.answer()
            return
        approved = parts[2] == "y"
        if not self.broker.resolve(parts[1], cb.from_user.id, approved):
            await cb.answer("Запрос устарел", show_alert=True)
            return
        await cb.answer("Подтверждено" if approved else "Отменено")
        text = getattr(cb.message, "text", None)
        if text:
            await self._edit_quiet(cb.message, text + ("\n\n✅ Подтверждено" if approved else "\n\n❌ Отменено"))

    async def _edit_quiet(self, message, text: str) -> None:
        try:
            await message.edit_text(text, reply_markup=None)
        except (TelegramAPIError, AttributeError):
            pass

    # --- подтверждение опасных действий ----------------------------------
    async def ask_confirm(self, message: Message, req: ConfirmRequest) -> bool:
        token, fut = self.broker.create(message.from_user.id)
        text = f"🔴 Нужно подтверждение\n{req.summary()}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"c:{token}:y"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"c:{token}:n"),
        ]])
        sent = None
        try:
            sent = await message.answer(text, reply_markup=kb)
            try:
                return await fut
            except asyncio.CancelledError:  # диспетчер отменил ожидание по таймауту
                await self._edit_quiet(sent, text + "\n\n⌛ Время вышло, отменено")
                raise
        finally:
            self.broker.discard(token)

    # --- запуск ----------------------------------------------------------
    def _build_dispatcher(self) -> Dispatcher:
        dp = Dispatcher()
        access = AccessMiddleware(self.allowed)
        dp.message.outer_middleware(access)
        dp.callback_query.outer_middleware(access)
        dp.message.register(self._on_file, F.document | F.photo)
        dp.message.register(self._on_voice, F.voice | F.audio | F.video_note)
        dp.message.register(self._on_text, F.text)
        dp.message.register(self._on_other)
        dp.callback_query.register(self._on_callback, F.data.startswith("c:"))
        return dp

    async def run(self) -> None:
        s = self.app.settings.secrets
        session = AiohttpSession(proxy=s.telegram_proxy) if s.telegram_proxy else None
        bot = Bot(token=s.telegram_bot_token, session=session)
        self.bot = bot
        try:
            try:
                me = await bot.me()
            except TelegramUnauthorizedError as e:
                raise TransportDisabled("Telegram отклонил токен: проверь TELEGRAM_BOT_TOKEN в .env") from e
            log.info("Telegram: бот @%s подключён", me.username)

            if self.app.settings.telegram_notify_on_start:
                for uid in sorted(self.allowed):
                    try:
                        await bot.send_message(uid, f"🟢 {self.app.settings.assistant_name} запущен на ПК")
                    except TelegramAPIError as e:
                        log.warning("Не удалось отправить приветствие %s: %s (нажми /start у бота?)", uid, e)

            dp = self._build_dispatcher()
            await dp.start_polling(
                bot, polling_timeout=30, handle_signals=False,
                allowed_updates=["message", "callback_query"],
            )
        finally:
            self.bot = None
            try:
                await bot.session.close()
            except Exception:  # noqa: BLE001
                pass
