import pytest

from jarvis.core.registry import ToolRegistry
from jarvis.core.tool import Risk, tool


def test_discover_finds_builtin_tools():
    r = ToolRegistry()
    r.discover("jarvis.tools")
    names = {t.name for t in r.all()}
    assert {"ping", "tools_help", "undo_last", "fs_list", "fs_mkdir", "fs_write_text", "fs_delete"} <= names
    assert not r.failed_modules


def test_risk_levels():
    r = ToolRegistry()
    r.discover("jarvis.tools")
    assert r.get("fs_delete").risk is Risk.RED
    assert r.get("fs_list").risk is Risk.GREEN


def test_duplicate_rejected():
    @tool("dup_a", "первый")
    def a(ctx): ...

    @tool("dup_a", "второй")
    def b(ctx): ...

    r = ToolRegistry()
    r.register(a.__tool__)
    with pytest.raises(ValueError):
        r.register(b.__tool__)


def test_bad_name_and_schema():
    with pytest.raises(ValueError):
        tool("Bad Name", "некорректное имя")
    with pytest.raises(Exception):
        tool("ok_name", "кривая схема", parameters={"properties": {"x": {"type": "nonsense"}}})