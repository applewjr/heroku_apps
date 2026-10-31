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

    /blossom's and /smush's GETs read today's board from those tables (the
    "today's board" sections of routes/blossom.py and routes/smush.py).
    ``secret_pass.py`` points at the production JawsDB, so the smoke tests
    that GET those pages would read it. tests/test_blossom_boards.py and
    tests/test_smush_boards.py put tables of their own in place of these.
    """
    import routes.blossom as blossom
    import routes.smush as smush

    for module in (blossom, smush):
        monkeypatch.setattr(module, "rows_logged_between", lambda first, last: [])
        monkeypatch.setattr(module, "_board_cache", {})


@pytest.fixture(autouse=True)
def no_ice_builds(monkeypatch):
    """Never really build an ICE COLD table in a test, for every test.

    A full-board build takes a minute and ~110 MB, and its pool reads ✗
    counts from smush_word_votes - the production JawsDB, through
    ``secret_pass.py``. Any read of a day's board can start one
    (routes/smush.py _prewarm_ice), so today's boards' cache here keeps its
    build threads in a list instead of running them, its sweeper never
    starts, and its caches start empty. tests/test_smush_ice.py runs the
    builds it needs by hand.
    """
    import routes.smush_ice as smush_ice
    from functions import smush_ice_cold as ice

    builds = []
    monkeypatch.setattr(smush_ice, "TODAY_TABLES",
                        smush_ice.today_tables(spawn=builds.append, may_start=None))
    monkeypatch.setattr(smush_ice, "EXACT", ice.ExactCache())
    monkeypatch.setattr(smush_ice, "SWEEPER", ice.IdleSweeper(
        lambda: (smush_ice.TODAY_TABLES, smush_ice.EXACT), start=lambda loop: None))
    monkeypatch.setattr(smush_ice, "_vote_counts", {})
    monkeypatch.setattr(smush_ice, "_board_results", {})
    return builds


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
