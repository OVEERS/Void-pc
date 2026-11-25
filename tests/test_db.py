import sqlite3

from jarvis.core.db import Database


def test_token_usage_counter(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    try:
        db.add_tokens("test", "model", 12, 8)
        db.add_tokens("test", "model", 5, 3)
        assert db.tokens_since("2000-01-01T00:00:00+00:00") == 28
    finally:
        db.close()


def test_existing_history_table_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE history (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, "
        "role TEXT NOT NULL, source TEXT NOT NULL, text TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO history(ts, role, source, text) VALUES (?, ?, ?, ?)",
        ("2026-01-01T00:00:00+00:00", "user", "telegram", "old entry"),
    )
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        db.add_history("user", "telegram", "new entry", "42")
        rows = db._query("SELECT user_id, text FROM history ORDER BY id")
        assert rows[0]["user_id"] is None
        assert rows[1]["user_id"] == "42"
    finally:
        db.close()
