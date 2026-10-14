"""/chainle: the page, solving, and the admin's round log
(routes/chainle.py, templates/chainle.html, templates/chainle_admin.html).

The solver runs on the tiny word set from test_chainle_solver.py. Every
database call goes through a scripted fake: secret_pass points at the
production JawsDB, so nothing here may reach a real connection.
"""

import json
import re
import time
from datetime import date

import pytest

from functions import chainle as ch
from test_blossom import _admin_headers, _ScriptedDB
from test_chainle_solver import _V, _arrays


@pytest.fixture
def tiny(monkeypatch):
    """The tiny word set in place of the shipped file, and a DB that fails
    any statement (the page itself must never need one)."""
    import routes.chainle as rc
    monkeypatch.setattr(rc, '_static', {'data': ch.Data(**_arrays()), 'failed_at': 0.0})
    monkeypatch.setattr(rc, '_assessed', {})

    def install(answers=None, rows=None, fail=None):
        db = _ScriptedDB(answers, rows, 1, fail)
        monkeypatch.setattr(rc, 'db_cursor', db)
        return db

    install(fail=' ')
    return install


@pytest.fixture
def no_data(monkeypatch):
    """The shipped file failed to load a moment ago."""
    import routes.chainle as rc
    monkeypatch.setattr(rc, '_static', {'data': None, 'failed_at': time.time()})


def _solve(client, clues, kind='bridge'):
    return client.post('/chainle', json={'clues': clues, 'kind': kind})


# --- the page ---------------------------------------------------------------

def test_page_shows_the_loaded_marker(client, tiny):
    html = client.get('/chainle').get_data(as_text=True)
    # The hourly web probe expects this marker (datasets/health_web.yaml).
    assert 'id="chn-data-ok"' in html
    assert f'from {len(_V)} words' in html


def test_faq_structured_data_parses_and_matches_the_visible_faq(client, tiny):
    # The FAQ is written twice: as JSON-LD for search engines and as the
    # visible section. A stray quote breaks the JSON; an edit to one copy
    # only makes them disagree.
    html = client.get('/chainle').get_data(as_text=True)
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    faq = next(d for d in map(json.loads, blocks) if d.get('@type') == 'FAQPage')
    visible = re.sub(r'<[^>]+>', '', re.sub(r'<script.*?</script>', '', html, flags=re.S))
    visible = ' '.join(visible.split())
    for q in faq['mainEntity']:
        assert q['name'] in visible
        assert q['acceptedAnswer']['text'] in visible


def test_page_says_unofficial_and_credits_every_source(client, tiny):
    # The disclaimer and the credits the sources' licences ask for
    # (datasets/CHAINLE_DATA.md has the reasons for each one).
    html = client.get('/chainle').get_data(as_text=True)
    for required in (
        'Not affiliated with or endorsed by Chainle',
        'GloVe', 'Pennington', 'Carlson', 'opendatacommons.org/licenses/pddl',
        'WordNet 3.0 Copyright 2006 by Princeton University',
        'Wiktionary contributors', 'creativecommons.org/licenses/by-sa/4.0',
        'Wikidata', 'creativecommons.org/publicdomain/zero/1.0', 'ENABLE',
        'wordfreq', 'Robyn Speer', 'SUBTLEX', 'Brysbaert', 'freely available', 'OpenSubtitles',
        'Google Books Ngrams', 'List of Dirty, Naughty, Obscene, and Otherwise Bad Words',
        'creativecommons.org/licenses/by/4.0',
    ):
        assert required in html, required


def test_page_names_no_other_chainle_site(client, tiny):
    # The page links to the game, but doesn't name the sites that copy it.
    html = client.get('/chainle').get_data(as_text=True).lower()
    assert 'chainle.io' in html
    assert 'chaindle' not in html and 'chainle.net' not in html


def test_page_never_touches_the_database(client, tiny):
    db = tiny(fail=' ')
    assert client.get('/chainle').status_code == 200
    assert not db.executed


def test_page_says_when_the_data_is_unavailable(client, no_data):
    resp = client.get('/chainle')
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'id="chn-data-ok"' not in html
    assert 'unavailable right now' in html


def test_loading_the_data_gives_freed_memory_back(monkeypatch):
    import routes.chainle as rc
    calls = []
    monkeypatch.setattr(rc, '_static', {'data': None, 'failed_at': 0.0})
    monkeypatch.setattr(rc.ch.Data, 'from_file', classmethod(lambda cls, path: ch.Data(**_arrays())))
    monkeypatch.setattr(rc, 'release_memory', lambda: calls.append(1))
    assert rc.get_data() is not None
    rc.get_data()
    assert calls == [1]   # once, on the load itself


def test_a_load_failure_alerts_and_waits_before_retrying(monkeypatch):
    import routes.chainle as rc
    tries, alerts = [], []

    def broken(cls, path):
        tries.append(path)
        raise OSError('missing')
    monkeypatch.setattr(rc, '_static', {'data': None, 'failed_at': 0.0})
    monkeypatch.setattr(rc.ch.Data, 'from_file', classmethod(broken))
    monkeypatch.setattr(rc.alerts, 'alert_throttled', lambda check, **kw: alerts.append(check))
    assert rc.get_data() is None
    assert rc.get_data() is None
    assert len(tries) == 1
    assert alerts == ['chainle_data_failed']


# --- solving ----------------------------------------------------------------

def test_a_bridge_round_returns_the_linking_word(client, tiny):
    resp = _solve(client, ['Snow', 'FOOT'])
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['clues'] == ['SNOW', 'FOOT']
    top = body['suggestions'][0]
    assert top['word'] == 'BALL'
    assert top['patterns'] == ['SNOW____', 'FOOT____']


def test_a_closest_meaning_round(client, tiny):
    body = _solve(client, ['big'], 'meaning').get_json()
    assert body['suggestions'][0]['word'] == 'LARGE'


def test_an_unknown_clue_is_reported(client, tiny):
    body = _solve(client, ['snow', 'qwxz']).get_json()
    assert body['unknown'] == ['QWXZ']
    assert body['suggestions'] == []


@pytest.mark.parametrize('payload', [
    {},
    {'clues': 'snow foot'},
    {'clues': ['snow']},
    {'clues': ['a', 'b', 'c', 'd']},
    {'clues': ['snow', 'SNOW']},
    {'clues': ['sn0w', 'foot']},
    {'clues': ['snow', 'x' * 21]},
    {'clues': [1, 2]},
    {'clues': ['snow', 'foot'], 'kind': 'chain'},
    {'clues': ['big', 'large'], 'kind': 'meaning'},
], ids=['empty', 'not-a-list', 'one-clue-bridge', 'four-clues', 'repeat', 'digit', 'too-long',
        'numbers', 'bad-kind', 'two-clue-meaning'])
def test_junk_requests_are_a_400(client, tiny, payload):
    assert client.post('/chainle', json=payload).status_code == 400


def test_a_non_json_body_is_a_415(client, tiny):
    resp = client.post('/chainle', data='x=1', content_type='application/x-www-form-urlencoded')
    assert resp.status_code == 415


def test_a_json_null_body_is_a_400(client, tiny):
    assert client.post('/chainle', data='null', content_type='application/json').status_code == 400


def test_solving_without_the_data_is_a_503(client, no_data):
    assert _solve(client, ['snow', 'foot']).status_code == 503


# --- the admin's round log -----------------------------------------------------------

ROWS = [
    (1, date(2026, 10, 14), 'chainle.io', 1, 'bridge', 'SNOW FOOT', 'BALL', 'BALL', 1000, None),
    (2, date(2026, 10, 14), 'chainle.io', 5, 'meaning', 'BIG', 'HUGE', 'LARGE', 950, 'took a while'),
    (3, date(2026, 10, 13), 'other', 2, 'bridge', 'SNOW QWXZ', 'BALL', None, None, None),
]


def _admin(tiny, monkeypatch, rows=ROWS, fail=None):
    db = tiny(answers={'SELECT COUNT(*)': (len(rows),)},
              rows={'FROM chainle_rounds ORDER BY': rows}, fail=fail)
    return db, _admin_headers(monkeypatch)


def _round_form(**changes):
    import routes.chainle as rc
    form = {'played_on': rc._today().isoformat(), 'site': 'chainle.io', 'round_no': '4',
            'kind': 'bridge', 'clue1': 'snow', 'clue2': 'Foot', 'clue3': 'basket',
            'answer': 'ball', 'guess': 'Ball', 'score': '1000', 'notes': ''}
    form.update(changes)
    return form


def test_admin_needs_the_password(client, tiny):
    assert client.get('/chainle_admin').status_code == 401
    assert client.post('/chainle_admin/log_round', data=_round_form()).status_code == 401
    assert client.post('/chainle_admin/delete_round', data={'id': '1'}).status_code == 401


def test_admin_page_rates_each_logged_round(client, tiny, monkeypatch):
    _, headers = _admin(tiny, monkeypatch)
    resp = client.get('/chainle_admin', headers=headers)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'SNOW + FOOT' in html and 'BALL' in html
    assert '<span class="rank hit">#1</span>' in html       # BALL is its first pick
    assert '<span class="rank near">#2</span>' in html      # HUGE comes after LARGE
    assert "a clue word isn't in its list" in html          # QWXZ
    assert 'Two-word bridge' in html and 'Closest meaning' in html
    assert 'Your average score: <b>975</b>' in html


def test_logging_a_round_saves_it_cleaned_up(client, tiny, monkeypatch):
    db, headers = _admin(tiny, monkeypatch)
    resp = client.post('/chainle_admin/log_round', data=_round_form(), headers=headers)
    assert resp.status_code == 302
    assert 'success=' in resp.headers['Location']
    [(sql, params)] = db.statements('INSERT INTO chainle_rounds')
    assert 'ON DUPLICATE KEY UPDATE' in sql
    import routes.chainle as rc
    assert params[:9] == (rc._today().isoformat(), 'chainle.io', 4, 'bridge', 'SNOW FOOT BASKET',
                          'BALL', 'BALL', 1000, None)
    assert db.commits == 1


def test_a_meaning_round_takes_one_clue_and_blank_extras(client, tiny, monkeypatch):
    db, headers = _admin(tiny, monkeypatch)
    form = _round_form(round_no='5', kind='', clue1='big', clue2='', clue3='', score='')
    client.post('/chainle_admin/log_round', data=form, headers=headers)
    [(_, params)] = db.statements('INSERT INTO chainle_rounds')
    assert params[2:5] == (5, 'meaning', 'BIG')
    assert params[7] is None


@pytest.mark.parametrize('changes', [
    {'played_on': '14/10/2026'},
    {'played_on': '2025-06-01'},
    {'site': 'example.com'},
    {'round_no': '6'},
    {'clue2': '', 'clue3': ''},
    {'clue1': 'sn0w'},
    {'guess': 'two words'},
    {'score': '1001'},
    {'score': '-5'},
], ids=['date-format', 'date-range', 'site', 'round', 'one-clue-bridge', 'digit', 'guess', 'score-high',
        'score-negative'])
def test_a_bad_round_is_refused_not_saved(client, tiny, monkeypatch, changes):
    db, headers = _admin(tiny, monkeypatch)
    resp = client.post('/chainle_admin/log_round', data=_round_form(**changes), headers=headers)
    assert resp.status_code == 302
    assert 'error=' in resp.headers['Location']
    assert not db.statements('INSERT INTO chainle_rounds')


def test_deleting_a_round(client, tiny, monkeypatch):
    db, headers = _admin(tiny, monkeypatch)
    resp = client.post('/chainle_admin/delete_round', data={'id': '3'}, headers=headers)
    assert resp.status_code == 302
    assert ('DELETE FROM chainle_rounds WHERE id = %s', (3,)) in db.executed
    assert db.commits == 1


def test_admin_survives_a_database_error(client, tiny, monkeypatch):
    _, headers = _admin(tiny, monkeypatch, fail='SELECT')
    assert client.get('/chainle_admin', headers=headers).status_code == 500
    _, headers = _admin(tiny, monkeypatch, fail='INSERT')
    resp = client.post('/chainle_admin/log_round', data=_round_form(), headers=headers)
    assert resp.status_code == 302
    assert 'Database+error' in resp.headers['Location']
