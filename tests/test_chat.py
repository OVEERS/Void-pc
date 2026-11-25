import asyncio

import pytest

from jarvis.transports.chat import ChatController
from jarvis.transports.cli import _print_safe


class FakePort:
    def __init__(self, confirm=True):
        self.texts, self.files, self.asked, self.confirm = [], [], [], confirm

    async def send_text(self, text):
        self.texts.append(text)

    async def send_file(self, path):
        self.files.append(path)

    async def ask_confirm(self, req):
        self.asked.append(req)
        return self.confirm


@pytest.fixture
def chat(app):
    return ChatController(app, "test")


def say(chat, text, port=None):
    port = port or FakePort()
    asyncio.run(chat.handle_text("1", text, port))
    return port


def test_ping(chat):
    assert "на связи" in say(chat, "ping").texts[-1]


def test_help_explains_syntax_and_shows_mkdir_example(chat):
    response = say(chat, "help").texts[-1]
    assert "КАК ПОЛЬЗОВАТЬСЯ" in response
    assert 'mkdir "моя папка"' in response
    assert 'fs_mkdir {"path": "папка/новая"}' in response
    assert "нужно: path" in response


def test_command_without_required_arguments_shows_usage(chat, app):
    response = say(chat, "fs_mkdir").texts[-1]
    assert "fs_mkdir" in response and "Пример:" in response
    assert app.db.recent_actions(1) == []


def test_easy_aliases_and_current_folder_defaults(chat, app):
    assert say(chat, 'mkdir "Новая папка"').texts[-1].startswith("✅")
    assert "Новая папка" in say(chat, "ls").texts[-1]
    assert "Новая папка" in say(chat, "tree").texts[-1]


def test_shorthand_and_kv(chat, app):
    say(chat, "fs_mkdir тест/папка")
    assert (app.sandbox.home / "тест" / "папка").is_dir()
    p = say(chat, 'fs_write_text path=тест/a.txt content="привет мир"')
    assert p.texts[-1].startswith("✅")
    assert (app.sandbox.home / "тест" / "a.txt").read_text(encoding="utf-8") == "привет мир"


def test_bad_command_not_dispatched(chat, app):
    p = say(chat, "fs_mkdir {плохой json")
    assert p.texts[-1].startswith("❌")
    assert app.db.recent_actions(1) == []


def test_unknown_tool(chat):
    assert say(chat, "взломать_пентагон").texts[-1].startswith("❌")


def test_red_asks_confirmation(chat, app):
    f = app.sandbox.home / "x.txt"
    f.write_text("1")
    p = say(chat, "fs_delete x.txt", FakePort(confirm=False))
    assert len(p.asked) == 1 and f.exists() and p.texts[-1].startswith("❌")
    p = say(chat, "fs_delete x.txt", FakePort(confirm=True))
    assert not f.exists() and p.texts[-1].startswith("✅")
    assert say(chat, "undo").texts[-1].startswith("✅") and f.exists()


def test_get_file_sends_attachment(chat, app):
    (app.sandbox.home / "r.txt").write_text("данные")
    p = say(chat, "fs_get_file r.txt")
    assert len(p.files) == 1 and p.files[0].name == "r.txt"
    assert say(chat, "fs_get_file нет_такого.txt").files == []


def test_get_file_rejects_empty_and_outside(chat, app):
    (app.sandbox.home / "e.txt").write_text("")
    assert say(chat, "fs_get_file e.txt").files == []
    assert say(chat, "fs_get_file ../../etc/passwd").files == []


def test_history(chat):
    say(chat, "ping")
    assert "ping" in say(chat, "history").texts[-1]


def test_history_logged(chat, app):
    say(chat, "ping")
    rows = app.db._query("SELECT role, source, user_id FROM history ORDER BY id")
    assert [(r["role"], r["source"], r["user_id"]) for r in rows] == [
        ("user", "test", "1"), ("assistant", "test", "1")
    ]


def test_history_command_is_scoped_by_user(app):
    controller = ChatController(app, "telegram")

    async def command(user_id, text):
        port = FakePort()
        await controller.handle_text(user_id, text, port)
        return port.texts[-1]

    async def scenario():
        await command("11", 'fs_mkdir {"path": "owner-one"}')
        await command("22", 'fs_write_text {"path": "owner-two.txt", "content": "private"}')
        one = await command("11", "history")
        two = await command("22", "history")
        assert "fs_mkdir" in one and "fs_write_text" not in one
        assert "fs_write_text" in two and "fs_mkdir" not in two

    asyncio.run(scenario())


def test_cli_output_fallback_for_unicode(monkeypatch):
    class AsciiStream:
        encoding = "ascii"

        def __init__(self):
            self.output = ""

        def write(self, text):
            text.encode("ascii")
            self.output += text
            return len(text)

        def flush(self):
            pass

    stream = AsciiStream()
    monkeypatch.setattr("sys.stdout", stream)
    _print_safe("✅ Ping")
    assert "Ping" in stream.output


def receive(chat, name, size, content=b"data", fail=False):
    async def download(dest):
        if fail:
            dest.write(b"half")
            raise ConnectionError("обрыв")
        dest.write(content)

    port = FakePort()
    path = asyncio.run(chat.handle_incoming_file("1", name, size, download, port))
    return path, port


def test_receive_saves_to_inbox_and_undo(chat, app):
    path, port = receive(chat, "doc.pdf", 100)
    assert path == app.sandbox.home / "inbox" / "doc.pdf" and path.read_bytes() == b"data"
    assert port.texts[-1].startswith("📥")
    assert not list((app.sandbox.home / "inbox").glob("*.part"))
    assert say(chat, "undo").texts[-1].startswith("✅") and not path.exists()


def test_receive_unique_names(chat, app):
    p1, _ = receive(chat, "a.txt", 1)
    p2, _ = receive(chat, "a.txt", 1)
    assert p1.name == "a.txt" and p2.name == "a (1).txt"


def test_receive_blocked_and_big(chat, app):
    path, port = receive(chat, "virus.exe", 10)
    assert path is None and port.texts[-1].startswith("❌")
    path, port = receive(chat, "big.zip", 25 * 1024 * 1024)
    assert path is None and "20 МБ" in port.texts[-1]


def test_receive_path_traversal_name(chat, app):
    path, _ = receive(chat, "../../evil.txt", 1)
    assert path == app.sandbox.home / "inbox" / "evil.txt"


def test_receive_failure_cleans_part(chat, app):
    path, port = receive(chat, "f.bin", 1, fail=True)
    assert path is None and port.texts[-1].startswith("❌")
    assert not list((app.sandbox.home / "inbox").iterdir())
    assert app.db.recent_actions(1)[0]["status"] == "error"


def test_receive_enforces_actual_download_size(chat, app, monkeypatch):
    monkeypatch.setattr("jarvis.transports.chat.MAX_INCOMING_BYTES", 4)
    path, port = receive(chat, "big.bin", 1, content=b"12345")
    assert path is None and "4 Б" in port.texts[-1]
    assert not list((app.sandbox.home / "inbox").iterdir())
    assert app.db.recent_actions(1)[0]["status"] == "failed"
