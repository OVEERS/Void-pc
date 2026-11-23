import asyncio

import pytest

from jarvis.core.app import build_app
from jarvis.core.config import Settings


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "ws"
    data = tmp_path / "data"
    root.mkdir()
    data.mkdir()
    settings = Settings(
        root=tmp_path, assistant_name="Тест", language="ru",
        allowed_roots=(root,), data_dir=data, tool_timeout_s=2.0, confirm_timeout_s=1.0,
    )
    a = build_app(settings)
    yield a
    a.db.close()


@pytest.fixture
def call(app):
    def _call(name, params=None, confirm=None):
        return asyncio.run(app.dispatcher.call(name, params or {}, source="test", confirm=confirm))
    return _call


async def yes(_req):
    return True


async def no(_req):
    return False
