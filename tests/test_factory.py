import dataclasses

from jarvis.core.config import Secrets
from jarvis.transports.factory import build_transports


def with_secrets(app, **kw):
    app.settings = dataclasses.replace(app.settings, secrets=Secrets(**kw))
    return app


def test_no_token_no_transport(app):
    assert build_transports(with_secrets(app)) == []


def test_token_without_ids_refuses_to_start(app):
    assert build_transports(with_secrets(app, telegram_bot_token="123:abc")) == []


def test_secrets_are_redacted_and_hidden():
    s = Secrets(telegram_bot_token="123:abc", telegram_proxy="http://u:p@h:1", cloud_api_key="k")
    assert "123:abc" not in repr(s) and "u:p@h" not in repr(s)
    assert set(s.secret_values()) == {"123:abc", "http://u:p@h:1", "k"}
