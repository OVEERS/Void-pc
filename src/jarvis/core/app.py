from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .db import Database
from .dispatcher import Dispatcher
from .registry import ToolRegistry
from .sandbox import PathSandbox
from .trash import Trash


@dataclass
class App:
    settings: Settings
    db: Database
    registry: ToolRegistry
    sandbox: PathSandbox
    trash: Trash
    dispatcher: Dispatcher


def build_app(settings: Settings) -> App:
    db = Database(settings.db_path)
    registry = ToolRegistry()
    registry.discover("jarvis.tools")
    sandbox = PathSandbox(settings.allowed_roots, settings.protected_paths)
    trash = Trash(settings.trash_dir, db)
    dispatcher = Dispatcher(settings, registry, db, sandbox, trash)
    return App(settings, db, registry, sandbox, trash, dispatcher)
