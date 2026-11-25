from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from .core.app import App, build_app
from .core.config import load_settings
from .core.logging_setup import setup_logging
from .core.singleton import SingleInstance
from .transports.base import Transport, TransportDisabled
from .transports.factory import build_transports

log = logging.getLogger("jarvis")


def _retry_after_seconds(error: BaseException) -> int | float | None:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        retry_after = getattr(current, "retry_after", None)
        if isinstance(retry_after, (int, float)) and retry_after >= 0:
            return retry_after
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return None


async def _supervise(t: Transport) -> None:
    """Держит транспорт живым: при сбое перезапускает с растущей паузой.
    Если сервер сам просит подождать (retry_after, flood control), ждём ровно столько, а не гоним запросы."""
    delay = 5.0
    while True:
        started = time.monotonic()
        wait, flood = delay, False
        try:
            await t.run()
            log.warning("Транспорт %s завершился сам, перезапускаю", t.name)
        except TransportDisabled as e:
            log.error("Транспорт %s выключен: %s", t.name, e)
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            retry_after = _retry_after_seconds(e)
            if retry_after is not None:
                flood = True
                wait = max(delay, retry_after + 2)
                log.warning("Транспорт %s: сервер просит подождать %s с, жду %.0f с", t.name, retry_after, wait)
            else:
                log.exception("Транспорт %s упал, перезапуск через %.0f с", t.name, delay)
        if time.monotonic() - started > 120:
            delay = 5.0  # работал долго, значит сбой разовый
        elif not flood:
            delay = min(delay * 2, 60.0)
        await asyncio.sleep(wait)


async def run_service(app: App) -> None:
    """Фоновый режим (его запускает Планировщик задач): транспорты + обслуживание."""
    transports = build_transports(app)
    log.info("%s запущен. Инструментов: %d, транспортов: %d",
             app.settings.assistant_name, len(app.registry.all()), len(transports))
    tasks = [asyncio.create_task(_supervise(t), name=t.name) for t in transports]
    try:
        while True:
            purged = app.trash.purge_older_than(app.settings.trash_retention_days)
            if purged:
                log.info("Корзина: удалено устаревших записей: %d", purged)
            await asyncio.sleep(6 * 3600)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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

    try:
        asyncio.run(run_cli(app))
    except KeyboardInterrupt:
        log.info("CLI остановлен пользователем")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
