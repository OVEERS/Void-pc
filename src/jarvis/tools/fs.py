from __future__ import annotations

import difflib
import fnmatch
import hashlib
import os
import secrets
import shutil
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..core.sandbox import SandboxError
from ..core.tool import Risk, ToolContext, ToolError, ToolResult, tool

MAX_WRITE_BYTES = 1_000_000
MAX_TREE_ITEMS = 500
MAX_SEARCH_RESULTS = 500
MAX_DIFF_CHARS = 20_000


def _resolve(ctx: ToolContext, path: str, *, allow_root: bool = True) -> Path:
    return ctx.sandbox.resolve(path, allow_root=allow_root, base=ctx.working_dir)


def _read_text(path: Path) -> tuple[bytes, str]:
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ToolError(f"Не удалось прочитать файл: {e}") from e
    if len(raw) > MAX_WRITE_BYTES:
        raise ToolError("Файл больше 1 МБ, редактирование этим инструментом недоступно")
    try:
        return raw, raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ToolError("Редактировать можно только текстовые файлы в UTF-8") from e


def _write_new_file(path: Path, content: bytes) -> None:
    try:
        handle = path.open("xb")
    except FileExistsError as e:
        raise ToolError(f"Файл уже существует: {path}") from e
    except OSError as e:
        raise ToolError(f"Не удалось создать файл: {e}") from e
    try:
        with handle:
            handle.write(content)
    except OSError as e:
        try:
            path.unlink()
        except OSError as cleanup_error:
            raise ToolError(f"Не удалось записать файл: {e}; не удалось убрать частичный файл: {cleanup_error}") from e
        raise ToolError(f"Не удалось записать файл: {e}") from e


def _file_diff(path: Path, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
        )
    )


def _preview_edit(ctx: ToolContext, path: Path, new_content: str) -> ToolResult:
    if not path.is_file():
        raise ToolError(f"Это не файл: {path}")
    raw, old_content = _read_text(path)
    encoded = new_content.encode("utf-8")
    if len(encoded) > MAX_WRITE_BYTES:
        raise ToolError("После изменения файл будет больше 1 МБ")
    diff = _file_diff(path, old_content, new_content)
    if not diff:
        return ToolResult(True, "Содержимое не изменится.")
    if len(diff) > MAX_DIFF_CHARS:
        raise ToolError("Diff слишком большой для безопасного предпросмотра; сократи объём изменений.")

    ctx.db.delete_expired_pending_edits(
        (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    )
    token = secrets.token_urlsafe(8)
    ctx.db.add_pending_edit(
        token,
        ctx.workspace_id,
        str(path),
        hashlib.sha256(raw).hexdigest(),
        new_content,
    )
    return ToolResult(
        True,
        f"Предпросмотр ({token}). Файл не изменён.\n"
        f"Для применения вызови fs_apply_edit с token={token}; Jarvis ещё попросит подтверждение.\n"
        f"```diff\n{diff}```",
        data={"token": token, "diff": diff},
    )


def _destination(
    ctx: ToolContext, source: Path, destination: str, *, unique_in_directory: bool = False
) -> Path:
    target = _resolve(ctx, destination)
    directory_destination = target.exists() and target.is_dir()
    if directory_destination:
        target = ctx.sandbox.resolve(str(target / source.name), allow_root=False)
    else:
        target = ctx.sandbox.resolve(str(target), allow_root=False)
    if not target.parent.is_dir():
        raise ToolError(f"Папки назначения не существует: {target.parent}")
    if target.exists():
        if not (unique_in_directory and directory_destination):
            raise ToolError(f"Файл или папка уже существует: {target}. Перезапись запрещена.")
        stem, suffix = target.stem, target.suffix
        index = 1
        while target.exists():
            target = target.with_name(f"{stem} ({index}){suffix}")
            index += 1
    if source == target or (source.is_dir() and source in target.parents):
        raise ToolError("Нельзя поместить папку внутрь самой себя")
    return target


def _validate_tree(ctx: ToolContext, path: Path) -> None:
    if path.is_symlink():
        raise ToolError(f"Операции с символическими ссылками запрещены: {path}")
    ctx.sandbox.resolve(str(path), allow_root=False)
    if path.is_dir():
        try:
            children = list(path.iterdir())
        except OSError as e:
            raise ToolError(f"Не удалось прочитать папку {path}: {e}") from e
        for child in children:
            _validate_tree(ctx, child)


def _copy_to(ctx: ToolContext, source: Path, target: Path) -> None:
    _validate_tree(ctx, source)
    try:
        if source.is_dir():
            shutil.copytree(source, target, copy_function=shutil.copy2)
        else:
            shutil.copy2(source, target)
    except OSError as e:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
        raise ToolError(f"Не удалось скопировать: {e}") from e


def _move_to(source: Path, target: Path) -> None:
    try:
        shutil.move(str(source), str(target))
    except OSError as e:
        raise ToolError(f"Не удалось переместить: {e}") from e


@tool(
    "fs_list",
    "Показать содержимое папки.",
    {"properties": {"path": {"type": "string"}, "max_items": {"type": "integer", "minimum": 1, "maximum": 1000}},
     "required": []},
)
def fs_list(ctx: ToolContext, path: str = ".", max_items: int = 200) -> ToolResult:
    p = _resolve(ctx, path)
    if not p.is_dir():
        raise ToolError(f"Это не папка: {p}")
    entries = []
    try:
        children = sorted(p.iterdir(), key=lambda c: (c.is_symlink() or not c.is_dir(), c.name.lower()))
    except OSError as e:
        raise ToolError(f"Не удалось прочитать папку: {e}") from e
    for child in children:
        if len(entries) >= max_items:
            break
        link = child.is_symlink()
        try:
            size = child.stat().st_size if child.is_file() and not link else None
        except OSError:
            size = None
        entries.append({"name": child.name, "dir": child.is_dir() and not link, "size": size, "symlink": link})
    lines = [
        ("🔗 " if e["symlink"] else "📁 " if e["dir"] else "📄 ")
        + e["name"]
        + (f"  ({e['size']} Б)" if e["size"] is not None else "")
        for e in entries
    ]
    return ToolResult(True, "\n".join(lines) or "(пусто)", data=entries)


@tool(
    "fs_tree",
    "Показать дерево папок; символические ссылки не обходятся.",
    {"properties": {
        "path": {"type": "string"},
        "max_depth": {"type": "integer", "minimum": 1, "maximum": 10},
        "max_items": {"type": "integer", "minimum": 1, "maximum": MAX_TREE_ITEMS},
    }, "required": []},
)
def fs_tree(ctx: ToolContext, path: str = ".", max_depth: int = 3, max_items: int = 200) -> ToolResult:
    root = _resolve(ctx, path)
    if not root.is_dir():
        raise ToolError(f"Это не папка: {root}")
    lines: list[str] = []

    def walk(folder: Path, depth: int) -> None:
        if depth >= max_depth or len(lines) >= max_items:
            return
        try:
            children = sorted(folder.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
        except OSError as e:
            raise ToolError(f"Не удалось прочитать папку {folder}: {e}") from e
        for child in children:
            if len(lines) >= max_items:
                return
            prefix = "  " * depth
            if child.is_symlink():
                lines.append(f"{prefix}🔗 {child.name}")
                continue
            try:
                ctx.sandbox.resolve(str(child))
            except SandboxError:
                continue
            is_dir = child.is_dir()
            lines.append(f"{prefix}{'📁' if is_dir else '📄'} {child.name}")
            if is_dir:
                walk(child, depth + 1)

    walk(root, 0)
    suffix = "\n… (достигнут лимит элементов)" if len(lines) >= max_items else ""
    return ToolResult(True, "\n".join(lines) + suffix if lines else "(пусто)", data=lines)


@tool(
    "fs_info",
    "Показать размер, даты и атрибуты файла или папки.",
    {"properties": {"path": {"type": "string"}}, "required": ["path"]},
)
def fs_info(ctx: ToolContext, path: str) -> ToolResult:
    p = _resolve(ctx, path)
    if not p.exists():
        raise ToolError(f"Не найдено: {p}")
    try:
        info = p.stat()
    except OSError as e:
        raise ToolError(f"Не удалось получить свойства: {e}") from e
    attrs: list[str] = []
    windows_attrs = getattr(info, "st_file_attributes", 0)
    for name, flag in (
        ("read-only", getattr(stat, "FILE_ATTRIBUTE_READONLY", 0x1)),
        ("hidden", getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)),
        ("system", getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)),
        ("archive", getattr(stat, "FILE_ATTRIBUTE_ARCHIVE", 0x20)),
        ("reparse-point", getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)),
    ):
        if windows_attrs & flag:
            attrs.append(name)
    if p.name.startswith(".") and "hidden" not in attrs:
        attrs.append("hidden")
    if not windows_attrs and not (info.st_mode & stat.S_IWUSR):
        attrs.append("read-only")
    data = {
        "path": str(p),
        "type": "directory" if p.is_dir() else "file",
        "size_bytes": info.st_size,
        "created": datetime.fromtimestamp(info.st_ctime).astimezone().isoformat(timespec="seconds"),
        "modified": datetime.fromtimestamp(info.st_mtime).astimezone().isoformat(timespec="seconds"),
        "attributes": attrs,
    }
    return ToolResult(
        True,
        f"{data['type']}: {p}\nРазмер: {info.st_size} Б\n"
        f"Создано: {data['created']}\nИзменено: {data['modified']}\n"
        f"Атрибуты: {', '.join(attrs) or 'нет'}",
        data=data,
    )


@tool(
    "fs_search",
    "Найти файлы и папки по имени или маске (например, *.txt).",
    {"properties": {
        "path": {"type": "string"},
        "pattern": {"type": "string", "minLength": 1},
        "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_RESULTS},
    }, "required": ["path", "pattern"]},
)
def fs_search(ctx: ToolContext, path: str, pattern: str, max_results: int = 100) -> ToolResult:
    root = _resolve(ctx, path)
    if not root.is_dir():
        raise ToolError(f"Это не папка: {root}")
    matches: list[str] = []

    def on_error(error: OSError) -> None:
        raise ToolError(f"Не удалось просканировать папку: {error}") from error

    for current, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=on_error):
        current_path = Path(current)
        safe_dirs = []
        for name in dirs:
            child = current_path / name
            if child.is_symlink():
                continue
            try:
                ctx.sandbox.resolve(str(child))
            except SandboxError:
                continue
            safe_dirs.append(name)
        dirs[:] = safe_dirs
        for name in [*dirs, *files]:
            child = current_path / name
            if child.is_symlink() or not fnmatch.fnmatchcase(name.casefold(), pattern.casefold()):
                continue
            try:
                ctx.sandbox.resolve(str(child))
            except SandboxError:
                continue
            matches.append(str(child.relative_to(root)))
            if len(matches) >= max_results:
                break
        if len(matches) >= max_results:
            break
    return ToolResult(True, "\n".join(matches) or "Совпадений нет", data=matches)


@tool(
    "fs_mkdir",
    "Создать папку (вместе с недостающими родительскими).",
    {"properties": {"path": {"type": "string"}}, "required": ["path"]},
    risk=Risk.YELLOW,
)
def fs_mkdir(ctx: ToolContext, path: str) -> ToolResult:
    p = _resolve(ctx, path)
    if p.exists():
        if p.is_dir():
            return ToolResult(True, f"Папка уже есть: {p}")
        raise ToolError(f"По этому пути лежит файл: {p}")
    top = p
    while not top.parent.exists():
        top = top.parent
    p.mkdir(parents=True)
    return ToolResult(True, f"Создана папка: {p}", undo={"steps": [{"kind": "remove_created", "path": str(top)}]})


@tool(
    "fs_create_file",
    "Создать новый файл из встроенного шаблона: empty, markdown, python или json.",
    {"properties": {
        "path": {"type": "string"},
        "template": {"type": "string", "enum": ["empty", "markdown", "python", "json"]},
    }, "required": ["path"]},
    risk=Risk.YELLOW,
)
def fs_create_file(ctx: ToolContext, path: str, template: str = "empty") -> ToolResult:
    p = _resolve(ctx, path, allow_root=False)
    if not p.parent.is_dir():
        raise ToolError(f"Папки не существует: {p.parent}. Сначала создай её (fs_mkdir).")
    if p.exists():
        raise ToolError(f"Файл уже существует: {p}")
    templates = {
        "empty": "",
        "markdown": f"# {p.stem}\n\n",
        "python": '"""Описание модуля."""\n\n',
        "json": "{}\n",
    }
    if template not in templates:
        raise ToolError(f"Неизвестный шаблон: {template}")
    _write_new_file(p, templates[template].encode("utf-8"))
    return ToolResult(True, f"Создан файл ({template}): {p}", undo={"steps": [{"kind": "remove_created", "path": str(p)}]})


@tool(
    "fs_write_text",
    "Создать текстовый UTF-8 файл. Для изменения существующего файла сначала покажи diff.",
    {"properties": {"path": {"type": "string"}, "content": {"type": "string"}, "overwrite": {"type": "boolean"}},
     "required": ["path", "content"]},
    risk=Risk.YELLOW,
)
def fs_write_text(ctx: ToolContext, path: str, content: str, overwrite: bool = False) -> ToolResult:
    p = _resolve(ctx, path, allow_root=False)
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise ToolError("Слишком большой файл для этой функции (лимит 1 МБ)")
    if not p.parent.is_dir():
        raise ToolError(f"Папки не существует: {p.parent}. Сначала создай её (fs_mkdir).")
    if p.exists():
        if overwrite:
            raise ToolError("Перезапись без предпросмотра запрещена. Используй fs_preview_write и fs_apply_edit.")
        raise ToolError(f"Файл уже существует: {p}. Для изменения используй fs_preview_write.")
    _write_new_file(p, data)
    return ToolResult(True, f"Записано {len(data)} Б в {p}", undo={"steps": [{"kind": "remove_created", "path": str(p)}]})


@tool(
    "fs_copy",
    "Скопировать файл или папку в путь назначения; существующие данные не перезаписываются.",
    {"properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
     "required": ["source", "destination"]},
    risk=Risk.YELLOW,
)
def fs_copy(ctx: ToolContext, source: str, destination: str) -> ToolResult:
    src = _resolve(ctx, source, allow_root=False)
    if not src.exists():
        raise ToolError(f"Не найдено: {src}")
    target = _destination(ctx, src, destination)
    _copy_to(ctx, src, target)
    return ToolResult(True, f"Скопировано: {src} → {target}", undo={"steps": [{"kind": "remove_created", "path": str(target)}]})


@tool(
    "fs_move",
    "Переместить файл или папку; существующие данные не перезаписываются.",
    {"properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
     "required": ["source", "destination"]},
    risk=Risk.YELLOW,
)
def fs_move(ctx: ToolContext, source: str, destination: str) -> ToolResult:
    src = _resolve(ctx, source, allow_root=False)
    if not src.exists():
        raise ToolError(f"Не найдено: {src}")
    _validate_tree(ctx, src)
    target = _destination(ctx, src, destination)
    _move_to(src, target)
    return ToolResult(
        True, f"Перемещено: {src} → {target}",
        undo={"steps": [{"kind": "restore_move", "source": str(src), "target": str(target)}]},
    )


@tool(
    "fs_rename",
    "Переименовать файл или папку в той же папке.",
    {"properties": {"path": {"type": "string"}, "new_name": {"type": "string", "minLength": 1}},
     "required": ["path", "new_name"]},
    risk=Risk.YELLOW,
)
def fs_rename(ctx: ToolContext, path: str, new_name: str) -> ToolResult:
    src = _resolve(ctx, path, allow_root=False)
    if not src.exists():
        raise ToolError(f"Не найдено: {src}")
    if Path(new_name).name != new_name or new_name in {".", ".."}:
        raise ToolError("Укажи только новое имя, без пути")
    target = _resolve(ctx, str(src.parent / new_name), allow_root=False)
    if target.exists():
        raise ToolError(f"Файл или папка уже существует: {target}")
    if src == target:
        raise ToolError("Новое имя совпадает с текущим")
    _move_to(src, target)
    return ToolResult(
        True, f"Переименовано: {src.name} → {target.name}",
        undo={"steps": [{"kind": "restore_move", "source": str(src), "target": str(target)}]},
    )


@tool(
    "fs_clipboard",
    "Положить путь в буфер Jarvis: mode=copy или mode=cut.",
    {"properties": {"path": {"type": "string"}, "mode": {"type": "string", "enum": ["copy", "cut"]}},
     "required": ["path", "mode"]},
    risk=Risk.YELLOW,
)
def fs_clipboard(ctx: ToolContext, path: str, mode: str) -> ToolResult:
    p = _resolve(ctx, path, allow_root=False)
    if not p.exists():
        raise ToolError(f"Не найдено: {p}")
    if mode == "cut":
        _validate_tree(ctx, p)
    ctx.db.set_clipboard(ctx.workspace_id, mode, str(p), str(ctx.working_dir))
    verb = "Вырезано" if mode == "cut" else "Скопировано"
    return ToolResult(True, f"{verb} в буфер Jarvis: {p}")


@tool(
    "fs_paste",
    "Вставить файл или папку из буфера Jarvis в указанную папку (по умолчанию текущую).",
    {"properties": {"destination": {"type": "string"}}, "required": []},
    risk=Risk.YELLOW,
)
def fs_paste(ctx: ToolContext, destination: str = ".") -> ToolResult:
    state = ctx.db.get_workspace_state(ctx.workspace_id)
    if state is None or not state["clipboard_mode"] or not state["clipboard_path"]:
        raise ToolError("Буфер Jarvis пуст. Используй fs_clipboard.")
    source = ctx.sandbox.resolve(state["clipboard_path"], allow_root=False)
    if not source.exists():
        raise ToolError("Объект из буфера больше не существует")
    destination_path = _resolve(ctx, destination)
    if not destination_path.is_dir():
        raise ToolError(f"Папка назначения не найдена: {destination_path}")
    if state["clipboard_mode"] == "cut" and source.parent == destination_path:
        ctx.db.clear_clipboard(ctx.workspace_id)
        return ToolResult(True, "Объект уже находится в этой папке; вырезание отменено.")
    target = _destination(ctx, source, destination, unique_in_directory=True)
    if state["clipboard_mode"] == "cut":
        _validate_tree(ctx, source)
        _move_to(source, target)
        ctx.db.clear_clipboard(ctx.workspace_id)
        return ToolResult(
            True, f"Перемещено из буфера: {source} → {target}",
            undo={"steps": [{"kind": "restore_move", "source": str(source), "target": str(target)}]},
        )
    _copy_to(ctx, source, target)
    return ToolResult(
        True, f"Вставлено из буфера: {source} → {target}",
        undo={"steps": [{"kind": "remove_created", "path": str(target)}]},
    )


@tool(
    "fs_pwd",
    "Показать текущую рабочую папку пользователя.",
)
def fs_pwd(ctx: ToolContext) -> str:
    return str(ctx.working_dir)


@tool(
    "fs_cd",
    "Изменить текущую рабочую папку пользователя.",
    {"properties": {"path": {"type": "string"}}, "required": ["path"]},
    risk=Risk.YELLOW,
)
def fs_cd(ctx: ToolContext, path: str) -> ToolResult:
    target = _resolve(ctx, path)
    if not target.is_dir():
        raise ToolError(f"Это не папка: {target}")
    ctx.db.set_working_dir(ctx.workspace_id, str(target))
    return ToolResult(True, f"Текущая папка: {target}")


@tool(
    "fs_preview_write",
    "Показать diff замены всего текста существующего UTF-8 файла; запись не выполняется.",
    {"properties": {"path": {"type": "string"}, "content": {"type": "string"}},
     "required": ["path", "content"]},
)
def fs_preview_write(ctx: ToolContext, path: str, content: str) -> ToolResult:
    return _preview_edit(ctx, _resolve(ctx, path, allow_root=False), content)


@tool(
    "fs_preview_replace",
    "Показать diff замены текста внутри UTF-8 файла; запись не выполняется.",
    {"properties": {
        "path": {"type": "string"},
        "old_text": {"type": "string", "minLength": 1},
        "new_text": {"type": "string"},
        "replace_all": {"type": "boolean"},
    }, "required": ["path", "old_text", "new_text"]},
)
def fs_preview_replace(
    ctx: ToolContext, path: str, old_text: str, new_text: str, replace_all: bool = True
) -> ToolResult:
    p = _resolve(ctx, path, allow_root=False)
    if not p.is_file():
        raise ToolError(f"Это не файл: {p}")
    _, old_content = _read_text(p)
    if old_text not in old_content:
        raise ToolError("Искомый текст не найден; файл не изменён")
    new_content = old_content.replace(old_text, new_text) if replace_all else old_content.replace(old_text, new_text, 1)
    return _preview_edit(ctx, p, new_content)


@tool(
    "fs_preview_append",
    "Показать diff добавления текста в конец UTF-8 файла; запись не выполняется.",
    {"properties": {"path": {"type": "string"}, "content": {"type": "string"}},
     "required": ["path", "content"]},
)
def fs_preview_append(ctx: ToolContext, path: str, content: str) -> ToolResult:
    p = _resolve(ctx, path, allow_root=False)
    if not p.is_file():
        raise ToolError(f"Это не файл: {p}")
    _, old_content = _read_text(p)
    return _preview_edit(ctx, p, old_content + content)


@tool(
    "fs_apply_edit",
    "Применить ранее показанный diff. Требует отдельного подтверждения.",
    {"properties": {"token": {"type": "string", "minLength": 1}}, "required": ["token"]},
    risk=Risk.RED,
)
def fs_apply_edit(ctx: ToolContext, token: str) -> ToolResult:
    pending = ctx.db.get_pending_edit(token, ctx.workspace_id)
    if pending is None:
        raise ToolError("Предпросмотр не найден или принадлежит другой сессии; покажи diff заново")
    p = ctx.sandbox.resolve(pending["path"], allow_root=False)
    if not p.is_file():
        raise ToolError("Файл из предпросмотра больше не существует")
    raw, _ = _read_text(p)
    if hashlib.sha256(raw).hexdigest() != pending["source_hash"]:
        raise ToolError("Файл изменился после предпросмотра; покажи diff заново")

    temp_path: Path | None = None
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    os.close(descriptor)
    temp_path = Path(temp_name)
    trash_id: int | None = None
    try:
        temp_path.write_text(pending["new_content"], encoding="utf-8")
        trash_id = ctx.trash.put(p, ctx.action_id)
        os.replace(temp_path, p)
    except OSError as e:
        if trash_id is not None:
            ctx.trash.restore(trash_id)
        raise ToolError(f"Не удалось применить изменение: {e}") from e
    finally:
        if temp_path.exists():
            temp_path.unlink()

    ctx.db.delete_pending_edit(token, ctx.workspace_id)
    return ToolResult(
        True,
        f"Изменения применены: {p}",
        undo={"steps": [
            {"kind": "remove_created", "path": str(p)},
            {"kind": "restore_trash", "trash_id": trash_id},
        ]},
    )


@tool(
    "fs_delete",
    "Удалить файл или папку (перемещение в корзину Jarvis, можно откатить).",
    {"properties": {"path": {"type": "string"}}, "required": ["path"]},
    risk=Risk.RED,
)
def fs_delete(ctx: ToolContext, path: str) -> ToolResult:
    p = _resolve(ctx, path, allow_root=False)
    if not p.exists():
        raise ToolError(f"Не найдено: {p}")
    trash_id = ctx.trash.put(p, ctx.action_id)
    return ToolResult(True, f"В корзине: {p.name}", undo={"steps": [{"kind": "restore_trash", "trash_id": trash_id}]})


MAX_SEND_BYTES = 50 * 1024 * 1024


@tool(
    "fs_get_file",
    "Прислать файл с ПК в чат.",
    {"properties": {"path": {"type": "string"}}, "required": ["path"]},
    risk=Risk.YELLOW,
)
def fs_get_file(ctx: ToolContext, path: str) -> ToolResult:
    p = _resolve(ctx, path)
    if not p.is_file():
        raise ToolError(f"Это не файл: {p}")
    size = p.stat().st_size
    if size == 0:
        raise ToolError("Файл пустой, Telegram такие не принимает")
    if size > MAX_SEND_BYTES:
        raise ToolError("Файл больше 50 МБ, Telegram не примет")
    return ToolResult(True, f"Отправляю: {p.name} ({size} Б)", data={"attach": str(p)})
