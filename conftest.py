"""Shared pytest fixtures.

Living at the repo root so the app's top-level modules (``app``, ``data``,
``functions`` ...) are importable from the test files.
"""

import queue

import pytest


@pytest.fixture(autouse=True)
def no_write_behind(monkeypatch):
    """Keep the write-behind drain off, for every test in the suite.

    ``secret_pass.py`` points at the production JawsDB, so a drain thread
    started during a test writes real rows to the live ``app_visits`` - and
    the routes that call ``log_page_visit`` are exactly the ones the smoke
    tests GET. Swapping the queue as well as stubbing the starter means each
    test also sees an empty queue it can assert against.
    """
    import extensions

    monkeypatch.setattr(extensions, "_ensure_drain", lambda: None)
    monkeypatch.setattr(extensions, "_log_queue",
                        queue.Queue(maxsize=extensions._LOG_QUEUE_MAX))


@pytest.fixture(autouse=True)
def no_board_reads(monkeypatch):
    """Start every test with an empty blossom_boards and smush_boards, and no
    board memo.

    /blossom_dev's and /smush_dev2's GETs read today's board from those
    tables (the "today's board" sections of routes/blossom.py and
    routes/smush.py), and so will /blossom's and /smush's once they go live.
    ``secret_pass.py`` points at the production JawsDB, so the smoke tests
    that GET those pages would read it. tests/test_blossom_boards.py and
    tests/test_smush_boards.py put tables of their own in place of these.
    """
    import routes.blossom as blossom
    import routes.smush as smush

    for module in (blossom, smush):
        monkeypatch.setattr(module, "rows_logged_between", lambda first, last: [])
        monkeypatch.setattr(module, "_board_cache", {})


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
