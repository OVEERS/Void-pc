import asyncio

from conftest import yes


def test_tree_search_and_properties(call, app):
    call("fs_mkdir", {"path": "docs/nested"})
    call("fs_write_text", {"path": "docs/nested/readme.txt", "content": "hello"})

    tree = call("fs_tree", {"path": "docs"})
    found = call("fs_search", {"path": "docs", "pattern": "*.txt"})
    info = call("fs_info", {"path": "docs/nested/readme.txt"})

    assert "nested" in tree.message and "readme.txt" in tree.message
    assert found.data == ["nested\\readme.txt"] or found.data == ["nested/readme.txt"]
    assert info.data["size_bytes"] == 5
    assert "modified" in info.data and "attributes" in info.data


def test_templates_copy_move_rename_and_undo(call, app):
    call("fs_mkdir", {"path": "docs"})
    made = call("fs_create_file", {"path": "docs/notes.md", "template": "markdown"})
    source = app.sandbox.home / "docs" / "notes.md"
    assert made.ok and source.read_text(encoding="utf-8").startswith("# notes")

    copied = call("fs_copy", {"source": "docs/notes.md", "destination": "copy.md"})
    assert copied.ok and (app.sandbox.home / "copy.md").exists()
    assert call("undo_last").ok and not (app.sandbox.home / "copy.md").exists()

    moved = call("fs_move", {"source": "docs/notes.md", "destination": "moved.md"})
    assert moved.ok and not source.exists()
    assert call("undo_last").ok and source.exists()

    renamed = call("fs_rename", {"path": "docs/notes.md", "new_name": "renamed.md"})
    assert renamed.ok and (source.parent / "renamed.md").exists()
    assert call("undo_last").ok and source.exists()


def test_clipboard_and_working_directory_are_per_user_and_persistent(app):
    async def dispatch(name, params=None, *, user="17"):
        return await app.dispatcher.call(
            name, params or {}, source="telegram", user_id=user, confirm=yes
        )

    async def scenario():
        await dispatch("fs_mkdir", {"path": "projects"})
        await dispatch("fs_mkdir", {"path": "projects/archive"})
        assert (await dispatch("fs_cd", {"path": "projects"})).ok
        assert (await dispatch("fs_write_text", {"path": "a.txt", "content": "one"})).ok
        assert (await dispatch("fs_clipboard", {"path": "a.txt", "mode": "cut"})).ok
        pasted = await dispatch("fs_paste", {"destination": "archive"})
        assert pasted.ok
        assert not (app.sandbox.home / "projects" / "a.txt").exists()
        assert (app.sandbox.home / "projects" / "archive" / "a.txt").read_text() == "one"
        assert (await dispatch("fs_pwd")).message.endswith("projects")
        assert (await dispatch("fs_pwd", user="18")).message.endswith("ws")

    asyncio.run(scenario())


def test_clipboard_copy_can_be_pasted_more_than_once(call, app):
    call("fs_write_text", {"path": "a.txt", "content": "one"})
    assert call("fs_clipboard", {"path": "a.txt", "mode": "copy"}).ok
    call("fs_mkdir", {"path": "copies"})
    assert call("fs_paste", {"destination": "copies"}).ok
    assert call("fs_paste", {"destination": "copies"}).ok
    assert (app.sandbox.home / "copies" / "a.txt").read_text() == "one"
    assert (app.sandbox.home / "copies" / "a (1).txt").read_text() == "one"


def test_edit_preview_rejects_stale_file_and_requires_confirmation(call, app):
    path = app.sandbox.home / "edit.txt"
    path.write_text("before", encoding="utf-8")
    preview = call("fs_preview_append", {"path": "edit.txt", "content": " after"})
    assert preview.ok and "+before after" in preview.message

    path.write_text("changed", encoding="utf-8")
    stale = call("fs_apply_edit", {"token": preview.data["token"]}, confirm=yes)
    assert not stale.ok and "изменился" in stale.message
    assert path.read_text(encoding="utf-8") == "changed"

    current = call("fs_preview_replace", {
        "path": "edit.txt", "old_text": "changed", "new_text": "done",
    })
    denied = call("fs_apply_edit", {"token": current.data["token"]})
    assert denied.status == "denied"
    assert path.read_text(encoding="utf-8") == "changed"
    assert call("fs_apply_edit", {"token": current.data["token"]}, confirm=yes).ok
    assert path.read_text(encoding="utf-8") == "done"


def test_edit_preview_is_bound_to_its_user(app):
    path = app.sandbox.home / "private.txt"
    path.write_text("before", encoding="utf-8")

    async def dispatch(name, params=None, *, user):
        return await app.dispatcher.call(
            name, params or {}, source="telegram", user_id=user, confirm=yes
        )

    async def scenario():
        preview = await dispatch(
            "fs_preview_write", {"path": "private.txt", "content": "after"}, user="owner"
        )
        denied = await dispatch(
            "fs_apply_edit", {"token": preview.data["token"]}, user="other"
        )
        assert not denied.ok
        assert path.read_text(encoding="utf-8") == "before"
        assert (await dispatch(
            "fs_apply_edit", {"token": preview.data["token"]}, user="owner"
        )).ok
        assert path.read_text(encoding="utf-8") == "after"

    asyncio.run(scenario())


def test_write_cannot_bypass_edit_preview(call, app):
    path = app.sandbox.home / "existing.txt"
    path.write_text("old", encoding="utf-8")
    result = call("fs_write_text", {"path": "existing.txt", "content": "new", "overwrite": True})
    assert not result.ok and "fs_preview_write" in result.message
    assert path.read_text(encoding="utf-8") == "old"
