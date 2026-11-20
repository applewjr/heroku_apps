"""Typeshift's crowd word corrections and /typeshift_admin's word tabs
(routes/typeshift.py, crowd.py).

Every database call goes through a scripted fake: secret_pass points at the
production JawsDB, so nothing here may reach a real connection. The board
is made up from dictionary words, never a real puzzle's.
"""

import pytest

from data import words
from test_blossom import _admin_headers, _ScriptedDB

# STONE, CRANE and BLIMP stacked.
COLUMNS = ['scb', 'trl', 'oai', 'nm', 'ep']
LISTED = 'stone'          # in the word list, and fits the board
NOT_LISTED = 'srone'      # fits the board, but isn't a word
ELSEWHERE = 'house'       # a word, but not one this board can make


def _votes(players, today=0, invalid_today=1):
    """Answers for the counts the vote path makes (see test_blossom._votes)."""
    return {
        "WHERE voter_hash = %s AND created_at": (today,),
        "WHERE voter_hash = %s AND vote = 'invalid'": (invalid_today,),
        "FROM typeshift_word_votes v WHERE v.word": (players,),
    }


@pytest.fixture
def crowd_db(monkeypatch):
    import crowd
    import routes.typeshift as rt

    def install(answers=None, rows=None, rowcount=1, fail=None):
        db = _ScriptedDB(answers, rows, rowcount, fail)
        monkeypatch.setattr(rt, 'db_cursor', db)
        monkeypatch.setattr(crowd, 'db_cursor', db)
        return db

    return install


def _vote(client, action, word, columns=COLUMNS, mode='help', path='/typeshift', **kw):
    return client.post(path, json={'action': action, 'word': word, 'columns': columns, 'mode': mode}, **kw)


def _tag():
    from routes.typeshift import puzzle_tag
    return puzzle_tag(tuple(COLUMNS))


def test_the_fixtures_are_what_they_claim():
    from functions import typeshift as ts
    assert LISTED in words and ts.fits_board(LISTED, COLUMNS)
    assert NOT_LISTED not in words and ts.fits_board(NOT_LISTED, COLUMNS)
    assert ELSEWHERE in words and not ts.fits_board(ELSEWHERE, COLUMNS)


def test_the_tag_is_each_columns_letters_in_order():
    assert _tag() == 'bcs:lrt:aio:mn:ep'
    from routes.typeshift import puzzle_tag
    assert puzzle_tag(('bsc', 'lrt', 'iao', 'mn', 'pe')) == _tag()


def test_reject_records_a_vote_for_this_board(client, crowd_db):
    db = crowd_db(_votes(players=1))
    resp = _vote(client, 'reject', LISTED)
    assert resp.status_code == 200 and resp.get_json() == {'status': 'success'}
    [(_sql, params)] = db.statements('INSERT INTO typeshift_word_votes')
    assert params[0] == LISTED and params[1] == 'invalid' and params[3] == _tag()


@pytest.mark.parametrize('word', [ELSEWHERE, NOT_LISTED], ids=['elsewhere', 'not_listed'])
def test_only_a_listed_word_that_fits_gets_a_vote(client, crowd_db, word):
    db = crowd_db(_votes(players=99))
    assert _vote(client, 'reject', word).get_json() == {'status': 'success'}
    assert not db.statements('INSERT INTO typeshift_word_votes')


def test_the_deciding_player_removes_the_word(client, crowd_db):
    import routes.typeshift as rt
    from crowd import remove_votes_needed
    db = crowd_db(_votes(players=remove_votes_needed(LISTED)))
    assert _vote(client, 'reject', LISTED).get_json() == {'status': 'success'}
    [(sql, params)] = db.statements('INSERT INTO typeshift_invalid_words')
    assert "'crowd'" in sql and params[0] == LISTED
    # The next solve re-reads the corrections rather than serving the memo.
    assert rt._index['expires'] == 0.0


def test_restore_takes_the_vote_back(client, crowd_db):
    db = crowd_db(_votes(players=0))
    assert _vote(client, 'restore', LISTED).get_json() == {'status': 'success'}
    assert db.statements('DELETE FROM typeshift_word_votes WHERE word = %s AND vote = %s AND voter_hash')


@pytest.mark.parametrize('word, players, expected, adds', [
    (ELSEWHERE, 0, 'not_on_board', False),
    (LISTED, 0, 'already_listed', False),
    (NOT_LISTED, 1, 'recorded', False),
    # The vote that adds the word gets the same reply as one that doesn't.
    (NOT_LISTED, 2, 'recorded', True),
])
def test_suggest_missing_outcomes(client, crowd_db, word, players, expected, adds):
    db = crowd_db(_votes(players=players))
    resp = _vote(client, 'suggest_missing', word)
    assert resp.status_code == 200
    assert resp.get_json() == {'status': 'success', 'result': expected}
    assert bool(db.statements('INSERT INTO typeshift_added_words')) == adds


def test_the_vote_log_keeps_the_page_mode(client, crowd_db):
    db = crowd_db(_votes(players=1))
    _vote(client, 'reject', LISTED, mode='solve')
    [row] = db.log_rows('typeshift')
    assert row['mode'] == 'solve' and row['puzzle'] == _tag()
    db = crowd_db(_votes(players=1))
    _vote(client, 'reject', LISTED, mode='other')
    [row] = db.log_rows('typeshift')
    assert row['mode'] is None


@pytest.mark.parametrize('body', [
    {'action': 'reject', 'word': LISTED},                                   # no board
    {'action': 'reject', 'word': LISTED, 'columns': ['ab']},
    {'action': 'reject', 'word': 'st', 'columns': COLUMNS},                 # not a word a board holds
    {'action': 'suggest_missing', 'word': 'st0ne', 'columns': COLUMNS},
])
def test_bad_votes_are_400s(client, crowd_db, body):
    crowd_db(_votes(players=1))
    assert client.post('/typeshift', json=body).status_code == 400


def test_corrections_reach_the_solver(client, crowd_db, monkeypatch):
    import routes.typeshift as rt
    monkeypatch.setattr(rt, '_index', {'value': None, 'expires': 0.0})
    crowd_db(rows={
        'SELECT word FROM typeshift_invalid_words': [(LISTED,)],
        'SELECT word FROM typeshift_added_words': [(NOT_LISTED,)],
    })
    reply = client.post('/typeshift', json={'columns': COLUMNS}).get_json()
    found = {w['word'] for w in reply['words']}
    assert LISTED not in found and NOT_LISTED in found and 'crane' in found


def test_a_change_landing_during_a_rebuild_is_not_lost(crowd_db, monkeypatch):
    import routes.typeshift as rt
    monkeypatch.setattr(rt, '_index', {'value': None, 'expires': 0.0})
    crowd_db()
    build = rt._build_index

    def change_lands_mid_build(invalid, added):
        rt._forget_words()                   # James's change commits while this builds
        return build(invalid, added)

    monkeypatch.setattr(rt, '_build_index', change_lands_mid_build)
    rt.get_index()
    # What was stored may predate the change: the next request builds again.
    assert rt._index['expires'] == 0.0
    # With no change meanwhile, the memo holds as usual.
    monkeypatch.setattr(rt, '_build_index', build)
    rt.get_index()
    assert rt._index['expires'] > 0.0


def test_corrections_never_come_from_another_game(client, crowd_db, monkeypatch):
    import routes.typeshift as rt
    monkeypatch.setattr(rt, '_index', {'value': None, 'expires': 0.0})
    db = crowd_db()
    client.post('/typeshift', json={'columns': COLUMNS})
    reads = [sql for sql, _params in db.executed if sql.startswith('SELECT word FROM')]
    assert reads == ['SELECT word FROM typeshift_invalid_words', 'SELECT word FROM typeshift_added_words']


def test_admin_word_form_and_undo(client, crowd_db, monkeypatch):
    db = crowd_db()
    auth = _admin_headers(monkeypatch)
    resp = client.post('/typeshift_admin/add_word', data={'word': 'Srone', 'word_type': 'missing'}, headers=auth)
    assert resp.status_code == 302
    [(sql, _params)] = db.statements('INSERT INTO typeshift_added_words')
    assert "ON DUPLICATE KEY UPDATE source = 'admin'" in sql
    resp = client.post('/typeshift_admin/remove_word', data={'word': 'srone', 'word_type': 'missing'}, headers=auth)
    assert resp.status_code == 302
    assert ('DELETE FROM typeshift_added_words WHERE word = %s', ('srone',)) in db.executed


def test_the_admin_page_renders_its_word_tabs(client, crowd_db, monkeypatch):
    from datetime import datetime
    when = datetime(2026, 11, 20, 8, 0)
    crowd_db(rows={'FROM typeshift_invalid_words': [('srone', when, 'crowd')]})
    html = client.get('/typeshift_admin', headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert 'Player Reports' in html and 'Removed Words' in html and 'Added Words' in html
    assert "Couldn't read the word lists" not in html
