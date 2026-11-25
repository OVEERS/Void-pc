from __future__ import annotations

import shutil

from .sandbox import PathSandbox
from .tool import ToolError
from .trash import Trash


def run_undo(sandbox: PathSandbox, trash: Trash, undo: dict) -> list[str]:
    """Выполняет шаги отката по порядку. Пути заново проверяются песочницей:
    данные в базе не считаются доверенными."""
    steps = undo.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ToolError("Нечего откатывать")

    done: list[str] = []
    for step in steps:
        kind = step.get("kind")
        if kind == "remove_created":
            path = sandbox.resolve(step["path"], allow_root=False)
            if path.exists():
                trash.put(path)  # откат сам обратим: файл уходит в корзину
                done.append(f"убрано: {path.name}")
        elif kind == "restore_trash":
            row = trash.db.get_trash(int(step["trash_id"]))
            if row is None:
                raise ToolError("Запись корзины не найдена")
            sandbox.resolve(row["original_path"], allow_root=False)
            restored = trash.restore(int(step["trash_id"]))
            done.append(f"восстановлено: {restored.name}")
        elif kind == "restore_move":
            source = sandbox.resolve(step["source"], allow_root=False)
            target = sandbox.resolve(step["target"], allow_root=False)
            if source.exists():
                raise ToolError(f"Место занято, переместить обратно нельзя: {source}")
            if not target.exists():
                raise ToolError(f"Не найдено для отката: {target}")
            source.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(target), str(source))
            except OSError as e:
                raise ToolError(f"Не удалось вернуть объект на место: {e}") from e
            done.append(f"возвращено: {source.name}")
        else:
            raise ToolError(f"Неизвестный шаг отката: {kind}")
    return done
