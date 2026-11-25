from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    tool        TEXT NOT NULL,
    risk        TEXT NOT NULL,
    source      TEXT NOT NULL,
    user_id     TEXT,
    params      TEXT,
    status      TEXT NOT NULL,
    result      TEXT,
    duration_ms INTEGER,
    undo        TEXT,
    undone      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS token_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    purpose           TEXT
);
CREATE TABLE IF NOT EXISTS history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    role    TEXT NOT NULL,
    source  TEXT NOT NULL,
    user_id TEXT,
    text    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trash (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    original_path TEXT NOT NULL,
    trash_path    TEXT NOT NULL,
    action_id     INTEGER,
    restored      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS workspace_state (
    owner          TEXT PRIMARY KEY,
    working_dir    TEXT NOT NULL,
    clipboard_mode TEXT,
    clipboard_path TEXT
);
CREATE TABLE IF NOT EXISTS pending_edits (
    token        TEXT PRIMARY KEY,
    owner        TEXT NOT NULL,
    path         TEXT NOT NULL,
    source_hash  TEXT NOT NULL,
    new_content  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(ts);
CREATE INDEX IF NOT EXISTS idx_tokens_ts ON token_usage(ts);
CREATE INDEX IF NOT EXISTS idx_pending_edits_created ON pending_edits(created_at);
CREATE INDEX IF NOT EXISTS idx_actions_owner ON actions(source, user_id, id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit] + "…[обрезано]"


class Database:
    """Тонкая обёртка над SQLite. Потокобезопасна (инструменты работают в потоках)."""

    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            history_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(history)")}
            if "user_id" not in history_columns:
                self._conn.execute("ALTER TABLE history ADD COLUMN user_id TEXT")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_owner ON history(source, user_id, id)"
            )

    # --- низкоуровневое -------------------------------------------------
    def _insert(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            return int(self._conn.execute(sql, args).lastrowid)

    def _execute(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, args).rowcount

    def _query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- журнал действий ------------------------------------------------
    def start_action(self, tool: str, risk: str, source: str, user_id: str | None, params: Any) -> int:
        p = _clip(json.dumps(params, ensure_ascii=False, default=str), 2000)
        return self._insert(
            "INSERT INTO actions(ts, tool, risk, source, user_id, params, status) "
            "VALUES (?,?,?,?,?,?, 'started')",
            (now_iso(), tool, risk, source, user_id, p),
        )

    def finish_action(
        self, action_id: int, status: str, result: str | None, duration_ms: int, undo: dict | None = None
    ) -> None:
        self._execute(
            "UPDATE actions SET status=?, result=?, duration_ms=?, undo=? WHERE id=?",
            (status, _clip(result, 1000), duration_ms, json.dumps(undo) if undo else None, action_id),
        )

    def last_undoable(self, source: str, user_id: str | None) -> sqlite3.Row | None:
        rows = self._query(
            "SELECT * FROM actions WHERE undo IS NOT NULL AND undone=0 AND status='ok' "
            "AND source=? AND user_id IS ? ORDER BY id DESC LIMIT 1",
            (source, user_id),
        )
        return rows[0] if rows else None

    def mark_undone(self, action_id: int) -> None:
        self._execute("UPDATE actions SET undone=1 WHERE id=?", (action_id,))

    def recent_actions(
        self, limit: int = 20, *, source: str | None = None, user_id: str | None = None
    ) -> list[sqlite3.Row]:
        if source is None:
            return self._query("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,))
        return self._query(
            "SELECT * FROM actions WHERE source=? AND user_id IS ? ORDER BY id DESC LIMIT ?",
            (source, user_id, limit),
        )

    # --- токены облака --------------------------------------------------
    def add_tokens(self, provider: str, model: str, prompt: int, completion: int, purpose: str = "") -> None:
        self._insert(
            "INSERT INTO token_usage(ts, provider, model, prompt_tokens, completion_tokens, purpose) "
            "VALUES (?,?,?,?,?,?)",
            (now_iso(), provider, model, prompt, completion, purpose),
        )

    def tokens_since(self, since_iso: str) -> int:
        rows = self._query(
            "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS n FROM token_usage WHERE ts >= ?",
            (since_iso,),
        )
        return int(rows[0]["n"])

    # --- история диалога ------------------------------------------------
    def add_history(self, role: str, source: str, text: str, user_id: str | None = None) -> None:
        self._insert(
            "INSERT INTO history(ts, role, source, user_id, text) VALUES (?,?,?,?,?)",
            (now_iso(), role, source, user_id, _clip(text, 4000)),
        )

    # --- корзина --------------------------------------------------------
    def add_trash(self, original: str, trash_path: str, action_id: int | None) -> int:
        return self._insert(
            "INSERT INTO trash(ts, original_path, trash_path, action_id) VALUES (?,?,?,?)",
            (now_iso(), original, trash_path, action_id),
        )

    def get_trash(self, trash_id: int) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM trash WHERE id=?", (trash_id,))
        return rows[0] if rows else None

    def mark_trash_restored(self, trash_id: int) -> None:
        self._execute("UPDATE trash SET restored=1 WHERE id=?", (trash_id,))

    def trash_older_than(self, cutoff_iso: str) -> list[sqlite3.Row]:
        return self._query("SELECT * FROM trash WHERE ts < ? AND restored=0", (cutoff_iso,))

    def delete_trash_row(self, trash_id: int) -> None:
        self._execute("DELETE FROM trash WHERE id=?", (trash_id,))

    # --- состояние рабочего пространства ------------------------------
    def get_workspace_state(self, owner: str) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM workspace_state WHERE owner=?", (owner,))
        return rows[0] if rows else None

    def set_working_dir(self, owner: str, path: str) -> None:
        self._execute(
            "INSERT INTO workspace_state(owner, working_dir) VALUES (?, ?) "
            "ON CONFLICT(owner) DO UPDATE SET working_dir=excluded.working_dir",
            (owner, path),
        )

    def set_clipboard(self, owner: str, mode: str, path: str, working_dir: str) -> None:
        self._execute(
            "INSERT INTO workspace_state(owner, working_dir, clipboard_mode, clipboard_path) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(owner) DO UPDATE SET "
            "clipboard_mode=excluded.clipboard_mode, clipboard_path=excluded.clipboard_path",
            (owner, working_dir, mode, path),
        )

    def clear_clipboard(self, owner: str) -> None:
        self._execute(
            "UPDATE workspace_state SET clipboard_mode=NULL, clipboard_path=NULL WHERE owner=?",
            (owner,),
        )

    # --- предпросмотры редактирования ----------------------------------
    def add_pending_edit(
        self, token: str, owner: str, path: str, source_hash: str, new_content: str
    ) -> None:
        self._insert(
            "INSERT INTO pending_edits(token, owner, path, source_hash, new_content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (token, owner, path, source_hash, new_content, now_iso()),
        )

    def get_pending_edit(self, token: str, owner: str) -> sqlite3.Row | None:
        rows = self._query(
            "SELECT * FROM pending_edits WHERE token=? AND owner=?", (token, owner)
        )
        return rows[0] if rows else None

    def delete_pending_edit(self, token: str, owner: str) -> None:
        self._execute("DELETE FROM pending_edits WHERE token=? AND owner=?", (token, owner))

    def delete_expired_pending_edits(self, cutoff_iso: str) -> int:
        return self._execute("DELETE FROM pending_edits WHERE created_at < ?", (cutoff_iso,))
