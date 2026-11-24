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
def no_circuits_db(monkeypatch):
    """Keep /circuits off the database, for every test.

    Its solves read James's added pairs and answers, and today's boards come
    from circuits_boards: ``secret_pass.py`` points at the production JawsDB.
    Every cursor in routes/circuits.py fails here, as in an outage (the page
    fails safe), and its memos start empty. tests/test_circuits_boards.py
    puts tables of its own in place of these.
    """
    import routes.circuits as circuits

    def down():
        raise RuntimeError("no database in tests")

    monkeypatch.setattr(circuits, "db_cursor", down)
    monkeypatch.setattr(circuits, "_boards_cache", {})
    monkeypatch.setattr(circuits, "_answers", {"value": {}, "expires": 0.0})
    monkeypatch.setattr(circuits, "_extra", {"value": None, "expires": 0.0, "gen": 0})
    monkeypatch.setattr(circuits, "_verdicts", {})


@pytest.fixture(scope="session")
def typeshift_index():
    """/typeshift's index of the plain word list, built once a session."""
    from data import words
    from functions import typeshift as ts
    return ts.Index.build(words)


@pytest.fixture(autouse=True)
def no_typeshift_db(monkeypatch, typeshift_index):
    """Keep /typeshift off the database, for every test.

    Its solves read Typeshift's word corrections and its word reports vote
    (both through crowd.py), and today's boards come from typeshift_boards:
    ``secret_pass.py`` points at the production JawsDB. Every cursor in
    routes/typeshift.py and crowd.py fails here, as in an outage (the page
    fails safe), the word list is the plain one with no corrections to read,
    and the boards memo starts empty. A test's own fixture can still put a
    fake in either place (tests/test_typeshift_boards.py,
    tests/test_typeshift_crowd.py and the other crowd games' tests do).
    """
    import crowd
    import routes.typeshift as typeshift

    def down():
        raise RuntimeError("no database in tests")

    monkeypatch.setattr(typeshift, "db_cursor", down)
    monkeypatch.setattr(crowd, "db_cursor", down)
    monkeypatch.setattr(typeshift, "_boards_cache", {})
    monkeypatch.setattr(typeshift, "_index", {"value": typeshift_index, "expires": float("inf")})


@pytest.fixture(autouse=True)
def no_four_by_three_db(monkeypatch):
    """Keep /4x3 off the database, for every test.

    Today's boards, players' marks and James's answers come from
    four_by_three_boards, and the solver's weights are fit on his answers:
    ``secret_pass.py`` points at the production JawsDB. Every cursor in
    routes/four_by_three.py fails here, as in an outage (the page fails
    safe), the memos start empty, and the weights are the defaults, as if
    already read. tests/test_four_by_three_boards.py puts tables of its own
    in place of these.
    """
    import routes.four_by_three as fbt_route
    from functions import four_by_three as fbt

    def down():
        raise RuntimeError("no database in tests")

    monkeypatch.setattr(fbt_route, "db_cursor", down)
    monkeypatch.setattr(fbt_route, "_boards_cache", {})
    monkeypatch.setattr(fbt_route, "_answers_cache", {})
    monkeypatch.setattr(fbt_route, "_tuning", {"value": (fbt.DEFAULT_WEIGHTS, 0), "expires": float("inf")})


@pytest.fixture(autouse=True)
def no_four_by_six_db(monkeypatch):
    """Keep /4x6's today's boards off the database, for every test.

    Boards are logged to and read from four_by_six_boards, and
    ``secret_pass.py`` points at the production JawsDB. Every cursor in
    routes/four_by_six.py fails here, as in an outage (the page fails safe),
    and its memo starts empty. tests/test_four_by_six_boards.py puts a table
    of its own in place of these.
    """
    import routes.four_by_six as fbs_route

    def down():
        raise RuntimeError("no database in tests")

    monkeypatch.setattr(fbs_route, "db_cursor", down)
    monkeypatch.setattr(fbs_route, "_boards_cache", {})


@pytest.fixture(autouse=True)
def no_chainle_best_db(monkeypatch):
    """Keep /chainle's confirmed words off the database, for every test.

    Every solve looks up its round in the memo of chainle_best, and
    ``secret_pass.py`` points at the production JawsDB. The memo starts
    read and empty here. tests/test_chainle_best.py fills it, or lets it
    read a table of its own.
    """
    import routes.chainle as chainle

    monkeypatch.setattr(chainle, "_best", {"value": {}, "expires": float("inf"), "gen": 0})


@pytest.fixture(autouse=True)
def no_gtp_boards_db(monkeypatch):
    """Keep /guess_the_phrase's today's board off the database, for every
    test.

    Boards are logged to and read from gtp_boards, and ``secret_pass.py``
    points at the production JawsDB. Every cursor in
    routes/guess_the_phrase.py fails here, as in an outage (the page fails
    safe), and its memo starts empty. tests/test_gtp_boards.py puts a table
    of its own in place of these.
    """
    import routes.guess_the_phrase as gtp

    def down():
        raise RuntimeError("no database in tests")

    monkeypatch.setattr(gtp, "db_cursor", down)
    monkeypatch.setattr(gtp, "_board_cache", {})


@pytest.fixture(autouse=True)
def no_wordiply_db(monkeypatch):
    """Keep /wordiply off the database, for every test.

    Starters are logged to and read from wordiply_boards, every search reads
    Wordiply's word corrections (through crowd.py), and ``secret_pass.py``
    points at the production JawsDB. Every cursor in routes/wordiply.py fails
    here, as in an outage (the page fails safe), its starter memo starts
    empty, and there are no corrections to read. tests/test_wordiply_boards.py
    and tests/test_wordiply_crowd.py put tables of their own in place of
    these.
    """
    import routes.wordiply as wordiply

    def down():
        raise RuntimeError("no database in tests")

    monkeypatch.setattr(wordiply, "db_cursor", down)
    monkeypatch.setattr(wordiply, "_starter_cache", {})
    monkeypatch.setattr(wordiply, "_corrections", {"value": (frozenset(), frozenset()), "expires": float("inf")})


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
                        smush_ice.today_tables(spawn=builds.append, may_start=None,
                                               must_shed=None, may_refresh=None))
    monkeypatch.setattr(smush_ice, "EXACT", ice.ExactCache())
    monkeypatch.setattr(smush_ice, "SIMS", ice.ExactCache())
    monkeypatch.setattr(smush_ice, "SWEEPER", ice.IdleSweeper(
        lambda: (smush_ice.TODAY_TABLES, smush_ice.EXACT), start=lambda loop: None))
    monkeypatch.setattr(smush_ice, "_vote_counts", {})
    monkeypatch.setattr(smush_ice, "_board_results", {})
    monkeypatch.setattr(smush_ice, "_openings", type(smush_ice._openings)())
    monkeypatch.setattr(smush_ice, "_built", {})
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
