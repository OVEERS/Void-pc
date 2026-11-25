import asyncio
from types import SimpleNamespace

import pytest

import jarvis.transports.telegram as telegram_module
from jarvis.core.dispatcher import ConfirmRequest
from jarvis.core.tool import Risk
from jarvis.transports.telegram import TelegramTransport


def test_startup_caches_bot_info_before_polling(monkeypatch, app):
    class FakeSession:
        closed = False

        async def close(self):
            self.closed = True

    class FakeBot:
        def __init__(self, **kwargs):
            self.session = FakeSession()
            self._me = None
            self.get_me_calls = 0

        async def get_me(self):
            self.get_me_calls += 1
            return SimpleNamespace(username="jarvis_test")

        async def me(self):
            if self._me is None:
                self._me = await self.get_me()
            return self._me

        async def send_message(self, user_id, text):
            pass

    bot = FakeBot()

    class FakeDispatcher:
        async def start_polling(self, bot, **kwargs):
            await bot.me()
            raise asyncio.CancelledError

    monkeypatch.setattr(telegram_module, "Bot", lambda **kwargs: bot)
    transport = TelegramTransport(app)
    transport._build_dispatcher = lambda: FakeDispatcher()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(transport.run())

    assert bot.get_me_calls == 1
    assert bot.session.closed
    assert transport.bot is None


def test_failed_confirmation_send_discards_pending_request(app):
    class FakeMessage:
        from_user = SimpleNamespace(id=42)

        async def answer(self, *args, **kwargs):
            raise RuntimeError("send failed")

    transport = TelegramTransport(app)
    request = ConfirmRequest("danger", Risk.RED, "Опасная операция", {})

    with pytest.raises(RuntimeError, match="send failed"):
        asyncio.run(transport.ask_confirm(FakeMessage(), request))
    assert not transport.broker._pending
