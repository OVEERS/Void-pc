import logging

from jarvis.core.config import Settings
from jarvis.core.logging_setup import setup_logging


def test_log_file_starts_with_utf8_bom(tmp_path):
    root_logger = logging.getLogger()
    existing_handlers = list(root_logger.handlers)
    existing_level = root_logger.level
    settings = Settings(
        root=tmp_path,
        assistant_name="Test",
        language="ru",
        allowed_roots=(tmp_path / "workspace",),
        data_dir=tmp_path / "data",
    )

    try:
        setup_logging(settings)
        logging.getLogger("test.bom").warning("Проверка кодировки")
        for handler in root_logger.handlers:
            handler.flush()

        log_path = settings.log_dir / "jarvis.log"
        assert log_path.read_bytes().startswith(b"\xef\xbb\xbf")
        assert "Проверка кодировки" in log_path.read_text(encoding="utf-8-sig")
    finally:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            if handler not in existing_handlers:
                handler.close()
        for handler in existing_handlers:
            root_logger.addHandler(handler)
        root_logger.setLevel(existing_level)
