from __future__ import annotations

import logging

from ..core.app import App
from .base import Transport

log = logging.getLogger(__name__)


def build_transports(app: App) -> list[Transport]:
    s = app.settings.secrets
    out: list[Transport] = []

    if s.telegram_bot_token:
        if not s.telegram_allowed_ids:
            log.error("Telegram: токен задан, но TELEGRAM_ALLOWED_IDS пуст. Бот НЕ запущен: "
                      "иначе им мог бы управлять кто угодно.")
        else:
            try:
                from .telegram import TelegramTransport
            except ImportError as e:
                log.error("Telegram: не удалось импортировать aiogram (%s). Выполни: pip install aiogram", e)
            else:
                out.append(TelegramTransport(app))
    else:
        log.info("Telegram: токен не задан, транспорт выключен")
    return out
