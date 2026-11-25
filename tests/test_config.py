from jarvis.core.config import load_settings


def test_settings_load_name_and_secrets_from_config_and_env(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.toml").write_text(
        '[assistant]\nname = "Нова"\n\n[paths]\nallowed_roots = ["workspace"]\ndata_dir = "data"\n',
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=synthetic-test-token\nTELEGRAM_ALLOWED_IDS=12, 34\n",
        encoding="utf-8",
    )
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_IDS"):
        monkeypatch.delenv(key, raising=False)

    settings = load_settings(tmp_path)

    assert settings.assistant_name == "Нова"
    assert settings.secrets.telegram_bot_token == "synthetic-test-token"
    assert settings.secrets.telegram_allowed_ids == (12, 34)
    assert "synthetic-test-token" not in repr(settings)
