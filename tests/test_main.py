from types import SimpleNamespace

import jarvis.__main__ as jarvis_main


def test_retry_after_seconds_reads_nested_exception():
    class RateLimited(Exception):
        retry_after = 32

    error = ExceptionGroup("transport failed", [ValueError("wrapper"), RateLimited()])

    assert jarvis_main._retry_after_seconds(error) == 32


def test_retry_after_seconds_returns_none_without_rate_limit():
    assert jarvis_main._retry_after_seconds(ValueError("failed")) is None


def test_cli_keyboard_interrupt_exits_cleanly(monkeypatch):
    app = SimpleNamespace(registry=SimpleNamespace(failed_modules={}))
    monkeypatch.setattr(jarvis_main, "load_settings", lambda: object())
    monkeypatch.setattr(jarvis_main, "setup_logging", lambda settings: None)
    monkeypatch.setattr(jarvis_main, "build_app", lambda settings: app)

    def interrupt(coro):
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(jarvis_main.asyncio, "run", interrupt)

    assert jarvis_main.main(["cli"]) == 0
