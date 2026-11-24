"""The corrected word lists' memo (crowd.CrowdList.corrected), shared by every
crowd game: a change made while the lists are being read is never hidden by
that read, whichever way they finish.

No database: every cursor is scripted here.
"""

import re
import threading
import time
from contextlib import contextmanager

import pytest

import crowd

GAMES = sorted(crowd.CROWD_GAMES)


class Table:
    """The removed words, as a read finds them. `during` runs once the read
    has its rows, before it returns them; `fail` makes the read fail there."""

    def __init__(self):
        self.removed = {'aaa'}
        self.reads = 0
        self.during = lambda: None
        self.fail = False

    @contextmanager
    def cursor(self):
        table = self

        class Cursor:
            rowcount = 1

            def execute(self, sql, params=None):
                self.sql = sql

            def fetchone(self):
                return None

            def fetchall(self):
                if '_invalid_words' not in self.sql:
                    return []
                table.reads += 1
                rows = [(w,) for w in sorted(table.removed)]
                during, table.during = table.during, lambda: None
                during()
                if table.fail:
                    raise RuntimeError('database down')
                return rows

        class Conn:
            def commit(self):
                pass
        yield Conn(), Cursor()


@pytest.fixture
def table(monkeypatch):
    t = Table()
    monkeypatch.setattr(crowd, 'db_cursor', t.cursor)
    return t


def _game(name='wordiply'):
    """A game's list, and its memo as the game's own on_change leaves it."""
    memo = {'value': None, 'expires': 0.0}
    return crowd.CrowdList(name, on_change=lambda: memo.update(expires=0.0)), memo


def _read(game, memo):
    return game.corrected(memo, lambda removed, added: frozenset(removed))


@pytest.mark.parametrize('name', GAMES)
def test_an_admin_change_during_a_read_shows_at_once(table, name):
    game, memo = _game(name)

    def change():
        table.removed.add('bbb')
        game.admin_set('bbb', 'invalid')
    table.during = change
    assert _read(game, memo) == {'aaa'}                  # the read it overtook
    assert _read(game, memo) == {'aaa', 'bbb'} and table.reads == 2
    assert _read(game, memo) == {'aaa', 'bbb'} and table.reads == 2      # and kept


def test_the_same_with_a_list_already_known(table):
    game, memo = _game()
    assert _read(game, memo) == {'aaa'}
    memo['expires'] = 0.0

    def change():
        table.removed.discard('aaa')
        game.admin_remove('aaa', 'invalid')
    table.during = change
    _read(game, memo)
    assert _read(game, memo) == frozenset()


@pytest.mark.parametrize('change', [
    lambda g: g.admin_set('x', 'invalid'), lambda g: g.admin_set('x', 'missing'),
    lambda g: g.admin_remove('x', 'invalid'), lambda g: g.admin_remove('x', 'missing'),
])
def test_every_admin_change_counts(table, change):
    game, memo = _game()
    table.during = lambda: change(game)
    _read(game, memo)
    assert memo['expires'] == 0.0


def test_a_players_removal_and_take_back_count(table, monkeypatch):
    game, memo = _game()
    monkeypatch.setattr(game, 'voter_hash', lambda: 'v')
    monkeypatch.setattr(game, '_count_players', lambda *a: 99)
    monkeypatch.setattr(game, '_apply_crowd_change', lambda *a: True)
    monkeypatch.setattr(game, '_log', lambda *a, **kw: None)

    class Cursor:
        rowcount = 1

        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return (0,)

        def fetchall(self):
            return []

    class Conn:
        def commit(self):
            pass

    @contextmanager
    def votes():
        yield Conn(), Cursor()
    monkeypatch.setattr(crowd, 'db_cursor', votes)
    before = game._changes
    assert game.record_vote('x', 'invalid', 'p') is True
    assert game._changes == before + 1
    monkeypatch.setattr(game, '_count_players', lambda *a: 0)
    game.retract_vote('x', 'invalid')
    assert game._changes == before + 2


def test_a_failed_read_a_change_overtook_is_tried_again_at_once(table):
    game, memo = _game()
    assert _read(game, memo) == {'aaa'}
    memo['expires'] = 0.0
    table.fail = True

    def change():
        table.removed.add('bbb')
        game.admin_set('bbb', 'invalid')
    table.during = change
    assert _read(game, memo) == {'aaa'}
    table.fail = False
    assert _read(game, memo) == {'aaa', 'bbb'}


def test_a_failed_read_still_waits_and_keeps_the_last_list(table):
    game, memo = _game()
    assert _read(game, memo) == {'aaa'}
    memo['expires'] = 0.0
    table.fail = True
    assert _read(game, memo) == {'aaa'} and table.reads == 2
    assert _read(game, memo) == {'aaa'} and table.reads == 2
    assert 0 < memo['expires'] - time.time() <= crowd.CORRECTIONS_RETRY_SECONDS


def test_a_read_nothing_changed_is_kept_for_hours(table):
    game, memo = _game()
    _read(game, memo)
    _read(game, memo)
    assert table.reads == 1
    assert memo['expires'] - time.time() > crowd.CORRECTIONS_REFRESH_SECONDS - 60


def test_a_stale_read_never_replaces_a_newer_one(table):
    # Another request's read, begun after the change, has been kept already.
    game, memo = _game()
    started, go, given = threading.Event(), threading.Event(), {}

    def hold():
        started.set()
        assert go.wait(10)
    table.during = hold
    thread = threading.Thread(target=lambda: given.update(slow=_read(game, memo)))
    thread.start()
    assert started.wait(10)
    table.removed.add('bbb')
    game.admin_set('bbb', 'invalid')
    assert _read(game, memo) == {'aaa', 'bbb'}
    go.set()
    thread.join(10)
    assert given['slow'] == {'aaa'}
    assert _read(game, memo) == {'aaa', 'bbb'} and table.reads == 2


def test_every_change_goes_through_the_counter():
    # A new kind of change that called on_change itself would skip it.
    source = open(crowd.__file__, encoding='utf-8').read()
    assert len(re.findall(r'self\.on_change\(\)', source)) == 1
    assert len(re.findall(r'self\._changed\(\)', source)) == 4
