from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import Database
from .tool import ToolError

log = logging.getLogger(__name__)


class Trash:
    """Собственная корзина Jarvis.

    Системная корзина Windows не даёт программно восстановить файл (send2trash умеет только
    отправлять), поэтому «удалить» = переместить в data/trash с записью в SQLite.
    Это даёт настоящий откат и автоочистку через N дней.
    """

    def __init__(self, base: Path, db: Database):
        self.base = base
        self.db = db
        self.base.mkdir(parents=True, exist_ok=True)

    def put(self, path: Path, action_id: int | None = None) -> int:
        if not path.exists() and not path.is_symlink():
            raise ToolError(f"Не найдено: {path}")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slot = self.base / f"{stamp}-{uuid.uuid4().hex[:8]}"
        slot.mkdir(parents=True)
        dest = slot / path.name
        shutil.move(str(path), str(dest))
        return self.db.add_trash(str(path), str(dest), action_id)

    def restore(self, trash_id: int) -> Path:
        row = self.db.get_trash(trash_id)
        if row is None or row["restored"]:
            raise ToolError(f"В корзине нет записи #{trash_id}")
        src = Path(row["trash_path"])
        target = Path(row["original_path"])
        if not src.exists():
            raise ToolError("Файл уже удалён из корзины")
        if target.exists():
            raise ToolError(f"Место занято, восстановить нельзя: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(target))
        self.db.mark_trash_restored(trash_id)
        try:
            src.parent.rmdir()
        except OSError:
            pass
        return target

    def purge_older_than(self, days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        count = 0
        for row in self.db.trash_older_than(cutoff):
            p = Path(row["trash_path"])
            try:
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p)
                elif p.exists() or p.is_symlink():
                    p.unlink()
                try:
                    p.parent.rmdir()
                except OSError:
                    pass
                self.db.delete_trash_row(row["id"])
                count += 1
            except OSError:
                log.exception("Не удалось очистить корзину: %s", p)
        return count
