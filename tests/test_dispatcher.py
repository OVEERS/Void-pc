import asyncio
import time

from conftest import no, yes

from jarvis.core.tool import Risk, ToolResult, tool


def test_unknown_tool(call):
    r = call("nope")
    assert not r.ok and r.status == "unknown_tool"


def test_invalid_params(call):
    r = call("fs_list", {})  # нет path
    assert r.status == "invalid"
    r = call("fs_list", {"path": "x", "лишнее": 1})
    assert r.status == "invalid"


def test_ping(call):
    r = call("ping")
    assert r.ok and "Тест" in r.message


def test_red_requires_confirmation(call, app):
    (app.sandbox.home / "a.txt").write_text("x")
    assert call("fs_delete", {"path": "a.txt"}).status == "denied"          # без confirm
    assert call("fs_delete", {"path": "a.txt"}, confirm=no).status == "denied"
    assert (app.sandbox.home / "a.txt").exists()
    assert call("fs_delete", {"path": "a.txt"}, confirm=yes).ok
    assert not (app.sandbox.home / "a.txt").exists()


def test_confirm_timeout_is_denial(call, app):
    (app.sandbox.home / "a.txt").write_text("x")

    async def slow(_):
        await asyncio.sleep(5)
        return True

    assert call("fs_delete", {"path": "a.txt"}, confirm=slow).status == "denied"
    assert (app.sandbox.home / "a.txt").exists()


def test_sandbox_denied_and_logged(call, app):
    r = call("fs_list", {"path": "../.."})
    assert r.status == "denied"
    assert app.db.recent_actions(1)[0]["status"] == "denied"


def test_timeout(app, call):
    @tool("slow_tool", "медленный", timeout_s=0.2)
    def slow(ctx):
        time.sleep(1)
        return "done"

    app.registry.register(slow.__tool__)
    assert call("slow_tool").status == "timeout"


def test_crash_is_contained(app, call):
    @tool("crashy", "падает")
    def crashy(ctx):
        raise RuntimeError("boom")

    app.registry.register(crashy.__tool__)
    r = call("crashy")
    assert r.status == "error" and "boom" not in r.message


def test_async_tool_and_bad_return(app, call):
    @tool("async_ok", "async")
    async def async_ok(ctx):
        return ToolResult(True, "ok!")

    @tool("bad_ret", "bad")
    def bad_ret(ctx):
        return 42

    app.registry.register(async_ok.__tool__)
    app.registry.register(bad_ret.__tool__)
    assert call("async_ok").message == "ok!"
    assert call("bad_ret").status == "error"
