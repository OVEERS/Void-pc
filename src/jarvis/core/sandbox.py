from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class SandboxError(Exception):
    """Путь вне разрешённой зоны или подозрительный. Текст показывается пользователю."""


def _canon(p: str | Path) -> str:
    # realpath раскрывает симлинки/junction и '..', normcase делает регистр Windows-независимым
    return os.path.normcase(os.path.realpath(p))


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # разные диски
        return False


class PathSandbox:
    """Единственная точка, через которую инструменты получают пути.

    Правило: инструмент никогда не открывает путь «как есть», только результат resolve().
    """

    def __init__(self, allowed_roots: Iterable[Path], protected: Iterable[Path] = ()):
        roots = list(allowed_roots)
        if not roots:
            raise ValueError("Нужна хотя бы одна разрешённая папка")
        self._home = Path(os.path.realpath(roots[0]))
        self._roots = [_canon(r) for r in roots]
        self._protected = [_canon(p) for p in protected]

    @property
    def home(self) -> Path:
        return self._home

    def resolve(self, raw: str, *, allow_root: bool = True) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise SandboxError("Пустой путь")
        if "\x00" in raw:
            raise SandboxError("Недопустимый символ в пути")

        # Alternate Data Streams (file.txt:secret) и зарезервированные имена устройств Windows
        if ":" in os.path.splitdrive(raw)[1]:
            raise SandboxError("Двоеточие в пути не поддерживается")
        if os.name == "nt":
            for part in Path(raw).parts:
                if part.split(".")[0].rstrip(" ").upper() in _WIN_RESERVED:
                    raise SandboxError("Зарезервированное имя устройства")

        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = self._home / p

        real = _canon(p)
        if not any(_inside(real, r) for r in self._roots):
            raise SandboxError("Путь вне разрешённых папок")
        if any(_inside(real, x) for x in self._protected):
            raise SandboxError("Это защищённая служебная папка Jarvis")
        if not allow_root and real in self._roots:
            raise SandboxError("Корень разрешённой папки трогать нельзя")
        return Path(os.path.realpath(p))
