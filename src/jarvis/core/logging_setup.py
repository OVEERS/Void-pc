from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import Settings


class RedactFilter(logging.Filter):
    """Вырезает значения секретов из любых логов."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secrets:
            msg = record.getMessage()
            for s in self._secrets:
                msg = msg.replace(s, "***")
            record.msg, record.args = msg, ()
        return True


def setup_logging(settings: Settings, level: int = logging.INFO) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    redact = RedactFilter(settings.secrets.secret_values())

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    file_h = RotatingFileHandler(
        settings.log_dir / "jarvis.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_h.setFormatter(fmt)
    file_h.addFilter(redact)
    root.addHandler(file_h)

    # pythonw.exe не имеет консоли: sys.stderr может быть None
    if sys.stderr is not None:
        con = logging.StreamHandler(sys.stderr)
        con.setFormatter(fmt)
        con.addFilter(redact)
        root.addHandler(con)
