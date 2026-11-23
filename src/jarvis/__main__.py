from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .core.app import App, build_app
from .core.config import load_settings
from .core.logging_setup import setup_logging
from .core.singleton import SingleInstance

log = logging.getLogger("jarvis")


async def run_service(app: App) -> None:
    """Фоновый режим (его запускает Планировщик задач). Сюда на следующих этапах
    подключаются транспорты (Telegram, Max) и голос."""
    log.info("%s запущен. Инструментов: %d", app.settings.assistant_name, len(app.registry.all()))
    while True:
        purged = app.trash.purge_older_than(app.settings.trash_retention_days)
        if purged:
            log.info("Корзина: удалено устаревших записей: %d", purged)
        await asyncio.sleep(6 * 3600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis")
    parser.add_argument("mode", nargs="?", choices=["cli", "run"], default="cli",
                        help="cli: консоль для проверки (по умолчанию); run: фоновый сервис")
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(settings)
    app = build_app(settings)
    for mod, err in app.registry.failed_modules.items():
        print(f"⚠ плагин {mod} не загрузился: {err}", file=sys.stderr)

    if args.mode == "run":
        lock = SingleInstance()
        if not lock.acquire():
            log.error("Уже запущен другой экземпляр, выхожу")
            return 3
        try:
            asyncio.run(run_service(app))
        except KeyboardInterrupt:
            log.info("Остановлен пользователем")
        except Exception:
            log.exception("Критическая ошибка сервиса")
            return 1
        finally:
            lock.release()
        return 0

    from .transports.cli import run_cli

    asyncio.run(run_cli(app))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
