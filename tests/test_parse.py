import asyncio

import pytest

from jarvis.core.registry import ToolRegistry
from jarvis.transports.common import (CommandError, ConfirmBroker, is_blocked_extension, parse_command,
                                      safe_filename, split_message, unique_path)


@pytest.fixture
def reg():
    r = ToolRegistry()
    r.discover("jarvis.tools")
    return r


def test_plain_and_aliases(reg):
    assert parse_command("ping", reg) == ("ping", {})
    assert parse_command("/ping@MyBot", reg) == ("ping", {})
    assert parse_command("/help", reg) == ("tools_help", {})
    assert parse_command("undo", reg) == ("undo_last", {})


def test_friendly_filesystem_aliases(reg):
    assert parse_command('mkdir "моя папка"', reg) == ("fs_mkdir", {"path": "моя папка"})
    assert parse_command("ls", reg) == ("fs_list", {})
    assert parse_command("ls проекты", reg) == ("fs_list", {"path": "проекты"})
    assert parse_command("touch notes.md", reg) == ("fs_create_file", {"path": "notes.md"})
    assert parse_command("delete notes.md", reg) == ("fs_delete", {"path": "notes.md"})


def test_json(reg):
    assert parse_command('fs_mkdir {"path": "a/b"}', reg) == ("fs_mkdir", {"path": "a/b"})


def test_smart_quotes_from_phone(reg):
    assert parse_command("fs_mkdir {“path”: “а/б”}", reg) == ("fs_mkdir", {"path": "а/б"})


def test_bad_json(reg):
    with pytest.raises(CommandError):
        parse_command("fs_mkdir {path: 1", reg)
    with pytest.raises(CommandError):
        parse_command("fs_mkdir {}x", reg)


def test_key_value_and_types(reg):
    n, p = parse_command('fs_write_text path=a.txt content="привет мир" overwrite=да', reg)
    assert n == "fs_write_text"
    assert p == {"path": "a.txt", "content": "привет мир", "overwrite": True}
    assert parse_command("fs_list path=x max_items=5", reg)[1] == {"path": "x", "max_items": 5}


def test_key_value_errors(reg):
    with pytest.raises(CommandError):
        parse_command("fs_write_text path=a.txt content=два слова", reg)   # пробелы без кавычек
    with pytest.raises(CommandError):
        parse_command("fs_list path=x max_items=много", reg)
    with pytest.raises(CommandError):
        parse_command("fs_write_text path=a content=b overwrite=может", reg)


def test_single_value_shorthand(reg):
    assert parse_command("fs_list проекты/тест", reg) == ("fs_list", {"path": "проекты/тест"})
    assert parse_command('fs_list "мои файлы"', reg) == ("fs_list", {"path": "мои файлы"})
    assert parse_command(r"fs_list C:\Users\me", reg)[1] == {"path": r"C:\Users\me"}


def test_shorthand_not_applicable(reg):
    with pytest.raises(CommandError):
        parse_command("fs_write_text какой-то текст", reg)   # два обязательных параметра
    with pytest.raises(CommandError):
        parse_command("")


def test_split_message():
    assert split_message("abc", 10) == ["abc"]
    text = "\n".join(["x" * 30] * 10)
    parts = split_message(text, 100)
    assert all(len(p) <= 100 for p in parts) and "\n".join(parts) == text
    huge = "y" * 250
    parts = split_message(huge, 100)
    assert [len(p) for p in parts] == [100, 100, 50]


def test_safe_filename():
    assert safe_filename("../../evil.txt") == "evil.txt"
    assert safe_filename("C:\\a\\b.txt") == "b.txt"
    assert safe_filename('a<b>:c?.txt') == "a_b__c_.txt"
    assert safe_filename("") == "file"
    assert safe_filename("CON.txt") == "_CON.txt"
    assert len(safe_filename("я" * 300 + ".pdf")) <= 120
    assert safe_filename("...") == "file"


def test_blocked_extensions():
    assert is_blocked_extension("setup.EXE") and is_blocked_extension("run.ps1")
    assert not is_blocked_extension("photo.jpg") and not is_blocked_extension("notes.txt")


def test_unique_path(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    assert unique_path(tmp_path, "a.txt").name == "a (1).txt"
    (tmp_path / "a (1).txt").write_text("1")
    assert unique_path(tmp_path, "a.txt").name == "a (2).txt"
    assert unique_path(tmp_path, "b.txt").name == "b.txt"


def test_confirm_broker():
    async def scenario():
        b = ConfirmBroker()
        token, fut = b.create(42)
        assert not b.resolve(token, 999, True)      # чужой пользователь
        assert not fut.done()
        assert b.resolve(token, 42, True)
        assert await fut is True
        assert not b.resolve(token, 42, False)      # уже решено
        assert not b.resolve("несуществующий", 42, True)
        t2, f2 = b.create(42)
        b.discard(t2)
        assert not b.resolve(t2, 42, True)

    asyncio.run(scenario())
