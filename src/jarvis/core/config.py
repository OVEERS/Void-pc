from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# src/jarvis/core/config.py -> корень проекта
DEFAULT_ROOT = Path(__file__).resolve().parents[3]


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Secrets:
    telegram_bot_token: str = field(default="", repr=False)
    telegram_allowed_ids: tuple[int, ...] = ()
    telegram_proxy: str = field(default="", repr=False)
    cloud_api_key: str = field(default="", repr=False)
    cloud_base_url: str = ""
    cloud_model: str = ""

    def secret_values(self) -> list[str]:
        return [v for v in (self.telegram_bot_token, self.telegram_proxy, self.cloud_api_key) if v]


@dataclass(frozen=True)
class Settings:
    root: Path
    assistant_name: str
    language: str
    allowed_roots: tuple[Path, ...]
    data_dir: Path
    tool_timeout_s: float = 30.0
    confirm_timeout_s: float = 60.0
    trash_retention_days: int = 30
    cloud_tokens_per_day: int = 50_000
    cloud_tokens_per_month: int = 1_000_000
    telegram_notify_on_start: bool = True
    secrets: Secrets = field(default_factory=Secrets, repr=False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jarvis.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def trash_dir(self) -> Path:
        return self.data_dir / "trash"

    @property
    def protected_paths(self) -> tuple[Path, ...]:
        """Эти пути инструменты не трогают, даже если они внутри разрешённых папок."""
        return (self.data_dir, self.root / "config", self.root / ".env")


def _resolve(base: Path, value: str) -> Path:
    p = Path(os.path.expandvars(value)).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def _parse_ids(raw: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in re.split(r"[,\s]+", raw.strip()) if x)
    except ValueError as e:
        raise ConfigError("TELEGRAM_ALLOWED_IDS: ожидаю числа через запятую") from e


def load_settings(root: Path | str | None = None) -> Settings:
    root_path = Path(root or os.environ.get("JARVIS_HOME") or DEFAULT_ROOT).resolve()
    load_dotenv(root_path / ".env")

    cfg: dict = {}
    cfg_file = root_path / "config" / "settings.toml"
    if cfg_file.exists():
        with cfg_file.open("rb") as f:
            cfg = tomllib.load(f)

    assistant = cfg.get("assistant", {})
    paths = cfg.get("paths", {})
    limits = cfg.get("limits", {})
    telegram = cfg.get("telegram", {})

    roots_raw = paths.get("allowed_roots") or ["workspace"]
    allowed = tuple(_resolve(root_path, r) for r in roots_raw)
    data_dir = _resolve(root_path, paths.get("data_dir", "data"))

    secrets = Secrets(
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_allowed_ids=_parse_ids(os.environ.get("TELEGRAM_ALLOWED_IDS", "")),
        telegram_proxy=os.environ.get("TELEGRAM_PROXY", ""),
        cloud_api_key=os.environ.get("CLOUD_API_KEY", ""),
        cloud_base_url=os.environ.get("CLOUD_BASE_URL", ""),
        cloud_model=os.environ.get("CLOUD_MODEL", ""),
    )

    settings = Settings(
        root=root_path,
        assistant_name=assistant.get("name", "Джарвис"),
        language=assistant.get("language", "ru"),
        allowed_roots=allowed,
        data_dir=data_dir,
        tool_timeout_s=float(limits.get("tool_timeout_s", 30)),
        confirm_timeout_s=float(limits.get("confirm_timeout_s", 60)),
        trash_retention_days=int(limits.get("trash_retention_days", 30)),
        cloud_tokens_per_day=int(limits.get("cloud_tokens_per_day", 50_000)),
        cloud_tokens_per_month=int(limits.get("cloud_tokens_per_month", 1_000_000)),
        telegram_notify_on_start=bool(telegram.get("notify_on_start", True)),
        secrets=secrets,
    )

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    for r in settings.allowed_roots:
        r.mkdir(parents=True, exist_ok=True)
    return settings
