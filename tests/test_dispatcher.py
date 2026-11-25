import asyncio
import time

from conftest import no, yes

from jarvis.core.tool import Risk, ToolResult, tool


def test_unknown_tool(call):
    r = call("nope")
    assert not r.ok and r.status == "unknown_tool"


def test_invalid_params(call):
    r = call("fs_search", {})  # нет path и pattern
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


def test_confirm_failure_is_reported_and_logged(call, app):
    (app.sandbox.home / "a.txt").write_text("x")

    async def broken(_):
        raise ConnectionError("telegram offline")

    result = call("fs_delete", {"path": "a.txt"}, confirm=broken)
    assert result.status == "error"
    assert result.message.startswith("\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c")
    assert (app.sandbox.home / "a.txt").exists()
    assert app.db.recent_actions(1)[0]["status"] == "error"


def test_cancelled_confirmation_is_audited(app):
    async def scenario():
        started = asyncio.Event()

        async def wait_forever(_):
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(app.dispatcher.call(
            "fs_delete", {"path": "unused"}, source="test", confirm=wait_forever
        ))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Cancellation should propagate")

    asyncio.run(scenario())
    assert app.db.recent_actions(1)[0]["status"] == "cancelled"


def test_undo_is_scoped_to_source_and_user(app):
    async def invoke(name, source, user_id, params=None):
        return await app.dispatcher.call(name, params or {}, source=source, user_id=user_id)

    async def scenario():
        assert (await invoke("fs_mkdir", "telegram", "11", {"path": "owned"})).ok
        denied = await invoke("undo_last", "telegram", "22")
        assert not denied.ok
        assert (app.sandbox.home / "owned").is_dir()
        assert (await invoke("undo_last", "telegram", "11")).ok
        assert not (app.sandbox.home / "owned").exists()

    asyncio.run(scenario())


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
