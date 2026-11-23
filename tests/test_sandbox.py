import os

import pytest

from jarvis.core.sandbox import PathSandbox, SandboxError


@pytest.fixture
def sb(tmp_path):
    root = tmp_path / "ws"
    (root / "inner").mkdir(parents=True)
    prot = tmp_path / "ws" / "secret"
    prot.mkdir()
    return PathSandbox([root], [prot]), root


def test_relative_goes_to_home(sb):
    s, root = sb
    assert s.resolve("a/b.txt") == (root / "a" / "b.txt").resolve()


def test_traversal_blocked(sb):
    s, _ = sb
    for bad in ("../x", "inner/../../x", "..", "/etc/passwd"):
        with pytest.raises(SandboxError):
            s.resolve(bad)


def test_prefix_trick_blocked(sb, tmp_path):
    s, root = sb
    sibling = tmp_path / "ws_evil"
    sibling.mkdir()
    with pytest.raises(SandboxError):
        s.resolve(str(sibling / "f.txt"))


def test_protected_blocked(sb):
    s, root = sb
    with pytest.raises(SandboxError):
        s.resolve("secret/key.txt")


def test_root_not_allowed_for_destructive(sb):
    s, root = sb
    assert s.resolve(".") == root.resolve()
    with pytest.raises(SandboxError):
        s.resolve(".", allow_root=False)


def test_empty_and_null(sb):
    s, _ = sb
    for bad in ("", "   ", "a\x00b"):
        with pytest.raises(SandboxError):
            s.resolve(bad)


@pytest.mark.skipif(os.name == "nt", reason="симлинки на Windows требуют прав; проверь вручную")
def test_symlink_escape_blocked(sb, tmp_path):
    s, root = sb
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, root / "link")
    with pytest.raises(SandboxError):
        s.resolve("link/file.txt")
