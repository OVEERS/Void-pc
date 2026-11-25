from conftest import yes


def test_mkdir_and_undo(call, app):
    assert call("fs_mkdir", {"path": "a/b/c"}).ok
    assert (app.sandbox.home / "a/b/c").is_dir()
    assert call("undo_last").ok
    assert not (app.sandbox.home / "a").exists()          # откатилась вся созданная цепочка


def test_write_no_overwrite_by_default(call, app):
    assert call("fs_write_text", {"path": "n.txt", "content": "1"}).ok
    r = call("fs_write_text", {"path": "n.txt", "content": "2"})
    assert not r.ok
    assert (app.sandbox.home / "n.txt").read_text() == "1"


def test_preview_apply_and_undo_restores_old(call, app):
    f = app.sandbox.home / "n.txt"
    call("fs_write_text", {"path": "n.txt", "content": "old"})
    preview = call("fs_preview_write", {"path": "n.txt", "content": "new"})
    assert preview.ok and "-old" in preview.message and "+new" in preview.message
    assert f.read_text() == "old"
    assert call("fs_apply_edit", {"token": preview.data["token"]}, confirm=yes).ok
    assert f.read_text() == "new"
    assert call("undo_last").ok
    assert f.read_text() == "old"


def test_delete_and_undo(call, app):
    f = app.sandbox.home / "d.txt"
    f.write_text("data")
    assert call("fs_delete", {"path": "d.txt"}, confirm=yes).ok
    assert not f.exists()
    assert call("undo_last").ok
    assert f.read_text() == "data"


def test_delete_folder_and_undo(call, app):
    (app.sandbox.home / "dir").mkdir()
    (app.sandbox.home / "dir" / "x.txt").write_text("1")
    assert call("fs_delete", {"path": "dir"}, confirm=yes).ok
    assert call("undo_last").ok
    assert (app.sandbox.home / "dir" / "x.txt").read_text() == "1"


def test_cannot_delete_root_or_data(call, app):
    assert call("fs_delete", {"path": "."}, confirm=yes).status == "denied"
    assert call("fs_delete", {"path": str(app.settings.data_dir)}, confirm=yes).status == "denied"


def test_undo_nothing(call):
    assert not call("undo_last").ok


def test_list(call, app):
    call("fs_mkdir", {"path": "p"})
    call("fs_write_text", {"path": "p/a.txt", "content": "hi"})
    r = call("fs_list", {"path": "p"})
    assert r.ok and "a.txt" in r.message


def test_trash_purge(app, call):
    f = app.sandbox.home / "old.txt"
    f.write_text("x")
    call("fs_delete", {"path": "old.txt"}, confirm=yes)
    assert app.trash.purge_older_than(30) == 0
    assert app.trash.purge_older_than(-1) == 1
