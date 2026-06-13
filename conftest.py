"""Shared pytest fixtures.

Living at the repo root so the app's top-level modules (``app``, ``data``,
``functions`` ...) are importable from the test files.
"""

import pytest


@pytest.fixture(scope="session")
def flask_app():
    # Importing app.py wires the blueprints, SSLify, ProxyFix, and pings Redis.
    from app import app
    from extensions import limiter

    app.config.update(TESTING=True)
    limiter.enabled = False  # don't let rate limits make the suite flaky
    return app


@pytest.fixture
def client(flask_app, monkeypatch):
    # Keep tests hermetic: never write to the real Redis logging stream.
    import routes.wordgames as wordgames
    monkeypatch.setattr(wordgames, "add_data_to_stream", lambda *a, **k: None)

    c = flask_app.test_client()
    # Heroku's router sets X-Forwarded-Proto; mimic it so Flask-SSLify doesn't
    # 301-redirect every test request to https.
    c.environ_base["HTTP_X_FORWARDED_PROTO"] = "https"
    return c
