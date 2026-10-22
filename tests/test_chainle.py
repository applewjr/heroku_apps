"""/chainle: the page, solving, and the admin's round log
(routes/chainle.py, templates/chainle.html, templates/chainle_admin.html).

The solver runs on the tiny word set from test_chainle_solver.py. Every
database call goes through a scripted fake: secret_pass points at the
production JawsDB, so nothing here may reach a real connection.
"""

import json
import re
import time
from datetime import date, datetime

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
    monkeypatch.setattr(rc, '_corrections', {'value': None, 'expires': 0.0})

    def install(answers=None, rows=None, fail=None):
        import crowd
        db = _ScriptedDB(answers, rows, 1, fail)
        monkeypatch.setattr(rc, 'db_cursor', db)
        # The reports and word lists go through crowd.py's own cursor.
        monkeypatch.setattr(crowd, 'db_cursor', db)
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
        'semantic vectors from', 'ConceptNet Numberbatch', 'Luminoso Technologies', 'GloVe', 'Pennington',
        'word2vec', 'Mikolov', 'OpenSubtitles 2016', 'Lison',
        'WordNet 3.0 Copyright 2006 by Princeton University',
        'Wiktionary contributors', 'creativecommons.org/licenses/by-sa/4.0',
        'Wikidata', 'creativecommons.org/publicdomain/zero/1.0', 'ENABLE',
        'wordfreq', 'Robyn Speer', 'SUBTLEX', 'Brysbaert', 'freely available', 'OpenSubtitles',
        'Google Books Ngrams', 'List of Dirty, Naughty, Obscene, and Otherwise Bad Words',
        'creativecommons.org/licenses/by/4.0',
    ):
        assert required in html, required


def test_page_no_longer_says_beta(client, tiny):
    html = client.get('/chainle').get_data(as_text=True)
    assert not re.search(r'\bbeta\b', html, re.I)


def test_answers_are_listed_without_a_show_button(client, tiny):
    # The words show, masked, as soon as there's a hint: no button opens them.
    html = client.get('/chainle').get_data(as_text=True)
    assert 'id="chn-answer-list"' in html
    assert 'chn-answers-toggle' not in html and 'Show possible answers' not in html


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
    assert body['suggestions'][0]['word'] == 'MAN'
    ball = next(s for s in body['suggestions'] if s['word'] == 'BALL')
    assert ball['type'] == 'link'
    assert ball['patterns'] == ['SNOW____', 'FOOT____']


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

# The last three columns are the solver version a round was logged with, its
# first pick and its rank for the answer: NULL before
# mysql_tables/chainle_rounds_version.sql, as here.
ROWS = [
    (1, date(2026, 10, 14), 'chainle.io', 'daily', 1, 'bridge', 'SNOW FOOT', 'MAN', 'BALL', 1000,
     '0.61 0.12', 150, 30000, None, None, None, None),
    (2, date(2026, 10, 14), 'chainle.io', 'unlimited', 5, 'meaning', 'BIG', 'HUGE', 'LARGE', 950,
     None, None, None, 'took a while', None, None, None),
    (3, date(2026, 10, 13), 'other', 'archive', 2, 'bridge', 'SNOW QWXZ', 'BALL', None, None,
     None, None, None, None, None, None, None),
]


def _admin(tiny, monkeypatch, rows=ROWS, fail=None, versioned=False, versions=()):
    """The admin login and a scripted database holding `rows`. versioned:
    the version columns exist, and `versions` is the by-version summary."""
    scripted = {'FROM chainle_rounds ORDER BY': rows}
    if versioned:
        scripted.update({'SHOW COLUMNS FROM chainle_rounds': [('solver_version',)],
                         'GROUP BY solver_version': list(versions)})
    db = tiny(answers={'SELECT COUNT(*)': (len(rows),)}, rows=scripted, fail=fail)
    return db, _admin_headers(monkeypatch)


def _round_form(**changes):
    import routes.chainle as rc
    form = {'played_on': rc._today().isoformat(), 'site': 'chainle.io', 'mode': 'unlimited', 'round_no': '4',
            'kind': 'bridge', 'clue1': 'snow', 'clue2': 'Foot', 'clue3': 'basket',
            'answer': 'ball', 'guess': 'Ball', 'score': '1000', 'link1': '.5', 'link2': '0.250',
            'link3': '1', 'game_rank': '#12', 'rank_of': '31,000', 'notes': ''}
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
    assert 'SNOW + FOOT' in html and 'MAN' in html
    assert '<span class="rank hit">#1</span>' in html       # MAN is its first pick
    assert 'Your guess BALL (1000):' in html
    assert '<span class="rank near">#2</span>' in html      # HUGE comes after LARGE
    assert "a clue word isn't in its list" in html          # QWXZ
    assert 'Two-word bridge' in html and 'Closest meaning' in html
    assert '&middot; unlimited &middot; round 5' in html
    assert 'closeness 0.61 / 0.12' in html and 'ranked #150 of 30,000' in html
    assert 'Your average score: <b>975</b>' in html


def test_admin_lists_the_newest_10_rounds_but_rates_them_all(client, tiny, monkeypatch):
    rows = [(rid, date(2026, 10, 14), 'chainle.io', 'daily', 1, 'bridge', 'SNOW FOOT', 'MAN', None, None,
             None, None, None, None, None, None, None) for rid in range(1, 13)]
    _, headers = _admin(tiny, monkeypatch, rows=rows)
    html = client.get('/chainle_admin', headers=headers).get_data(as_text=True)
    assert html.count('class="round-card"') == 10
    assert 'Showing the latest 10 of 12.' in html
    summary = html.split('class="summary"')[1].split('</table>')[0]
    assert '<td>12</td>' in summary


def test_logging_a_round_saves_it_cleaned_up(client, tiny, monkeypatch):
    import routes.chainle as rc
    db, headers = _admin(tiny, monkeypatch)
    resp = client.post('/chainle_admin/log_round', data=_round_form(), headers=headers)
    assert resp.status_code == 302
    [(sql, params)] = db.statements('INSERT INTO chainle_rounds')
    assert 'ON DUPLICATE KEY UPDATE' in sql
    saved = dict(zip(rc.ROUND_COLUMNS, params))
    del saved['created_at']
    assert saved == {'played_on': rc._today().isoformat(), 'site': 'chainle.io', 'mode': 'unlimited',
                     'round_no': 4, 'kind': 'bridge', 'clues': 'SNOW FOOT BASKET', 'answer': 'BALL',
                     'guess': 'BALL', 'score': 1000, 'closeness': '0.5 0.25 1', 'game_rank': 12,
                     'rank_of': 31000, 'notes': None}
    assert db.commits == 1
    # Back to the form for the same day's next round.
    where = resp.headers['Location']
    assert 'success=' in where and 'round=5' in where and f'date={rc._today().isoformat()}' in where


def test_after_the_last_round_the_form_starts_over(client, tiny, monkeypatch):
    _, headers = _admin(tiny, monkeypatch)
    form = _round_form(round_no='5', kind='', clue1='big', clue2='', clue3='')
    where = client.post('/chainle_admin/log_round', data=form, headers=headers).headers['Location']
    assert 'success=' in where
    assert 'round=' not in where and 'date=' not in where


def test_the_form_opens_on_the_round_it_is_sent_to(client, tiny, monkeypatch):
    _, headers = _admin(tiny, monkeypatch)
    html = client.get('/chainle_admin?date=2026-10-13&round=3', headers=headers).get_data(as_text=True)
    assert 'name="played_on" value="2026-10-13"' in html
    assert 'name="round_no" value="3" checked' in html
    assert 'id="picks-group"' in html    # the solver's picks, to play from this page


def test_the_form_ignores_junk_defaults(client, tiny, monkeypatch):
    import routes.chainle as rc
    _, headers = _admin(tiny, monkeypatch)
    html = client.get('/chainle_admin?date=<b>&round=9', headers=headers).get_data(as_text=True)
    assert f'name="played_on" value="{rc._today().isoformat()}"' in html
    assert 'name="round_no" value="1" checked' in html


def test_a_meaning_round_takes_one_clue_and_blank_extras(client, tiny, monkeypatch):
    db, headers = _admin(tiny, monkeypatch)
    import routes.chainle as rc
    form = _round_form(round_no='5', kind='', clue1='big', clue2='', clue3='', score='', mode='')
    client.post('/chainle_admin/log_round', data=form, headers=headers)
    [(_, params)] = db.statements('INSERT INTO chainle_rounds')
    saved = dict(zip(rc.ROUND_COLUMNS, params))
    assert (saved['round_no'], saved['kind'], saved['clues']) == (5, 'meaning', 'BIG')
    assert saved['score'] is None
    assert saved['mode'] == 'daily'     # the default
    assert saved['closeness'] == '0.5'  # one clue, one closeness


@pytest.mark.parametrize('changes', [
    {'played_on': '14/10/2026'},
    {'played_on': '2025-06-01'},
    {'site': 'example.com'},
    {'mode': 'weekly'},
    {'round_no': '6'},
    {'clue2': '', 'clue3': ''},
    {'clue1': 'sn0w'},
    {'guess': 'two words'},
    {'score': '1001'},
    {'score': '-5'},
    {'link1': '1.5'},
    {'link2': 'abc'},
    {'link3': ''},
    {'game_rank': 'first'},
    {'game_rank': '500', 'rank_of': '400'},
], ids=['date-format', 'date-range', 'site', 'mode', 'round', 'one-clue-bridge', 'digit', 'guess',
        'score-high', 'score-negative', 'closeness-high', 'closeness-text', 'closeness-missing-one',
        'rank-text', 'rank-past-count'])
def test_a_bad_round_is_refused_not_saved(client, tiny, monkeypatch, changes):
    db, headers = _admin(tiny, monkeypatch)
    resp = client.post('/chainle_admin/log_round', data=_round_form(**changes), headers=headers)
    assert resp.status_code == 302
    assert 'error=' in resp.headers['Location']
    assert 'round=' in resp.headers['Location']     # back on the same round
    assert not db.statements('INSERT INTO chainle_rounds')


def test_deleting_a_round(client, tiny, monkeypatch):
    db, headers = _admin(tiny, monkeypatch)
    resp = client.post('/chainle_admin/delete_round', data={'id': '3'}, headers=headers)
    assert resp.status_code == 302
    assert ('DELETE FROM chainle_rounds WHERE id = %s', (3,)) in db.executed
    assert db.commits == 1


# --- which solver version a round was logged with -------------------------------------

def test_the_solver_version_is_a_short_code_that_follows_the_data(tiny, monkeypatch):
    import routes.chainle as rc
    version = rc.solver_version()
    assert re.fullmatch(r'[0-9a-f]{8}', version)
    assert rc.solver_version() == version
    other = ch.pack(_V, zipf={w: 4.0 for w in _V}, answer=set(_V))
    monkeypatch.setattr(rc, '_static', {'data': ch.Data(**other), 'failed_at': 0.0})
    assert rc.solver_version() != version


def test_logging_works_before_the_version_columns_exist(client, tiny, monkeypatch):
    # Prod may run this code before mysql_tables/chainle_rounds_version.sql.
    import routes.chainle as rc
    db, headers = _admin(tiny, monkeypatch)
    resp = client.post('/chainle_admin/log_round', data=_round_form(), headers=headers)
    assert 'success=' in resp.headers['Location']
    [(sql, params)] = db.statements('INSERT INTO chainle_rounds')
    assert 'solver_version' not in sql and len(params) == len(rc.ROUND_COLUMNS)


def test_logging_saves_what_the_solver_made_of_the_round(client, tiny, monkeypatch):
    import routes.chainle as rc
    db, headers = _admin(tiny, monkeypatch, versioned=True)
    client.post('/chainle_admin/log_round', data=_round_form(), headers=headers)
    [(sql, params)] = db.statements('INSERT INTO chainle_rounds')
    saved = dict(zip(rc.ROUND_COLUMNS + rc.VERSION_COLUMNS, params))
    assert 'solver_top = new.solver_top' in sql
    # SNOW + FOOT + BASKET: BALL is its first pick, and the answer.
    assert (saved['solver_version'], saved['solver_top'], saved['answer_rank']) == (rc.solver_version(), 'BALL', 1)


def test_admin_page_works_before_the_version_columns_exist(client, tiny, monkeypatch):
    db, headers = _admin(tiny, monkeypatch)
    html = ' '.join(client.get('/chainle_admin', headers=headers).get_data(as_text=True).split())
    assert 'once mysql_tables/chainle_rounds_version.sql has run' in html
    assert 'id="by-version"' not in html and 'Logged with solver' not in html
    assert db.statements('NULL, NULL, NULL FROM chainle_rounds')
    assert not db.statements('GROUP BY solver_version')


def test_admin_page_splits_the_rounds_by_solver_version(client, tiny, monkeypatch):
    import routes.chainle as rc
    now = rc.solver_version()
    rows = [ROWS[0][:14] + (now, 'MAN', 1), ROWS[1][:14] + ('0badc0de', 'LARGE', 2)]
    versions = [(now, 1, 1000, 1, 1, 1, 0, date(2026, 10, 14), date(2026, 10, 14)),
                ('0badc0de', 3, 812.4, 2, 1, 2, 2, date(2026, 10, 12), date(2026, 10, 13))]
    _, headers = _admin(tiny, monkeypatch, rows=rows, versioned=True, versions=versions)
    html = client.get('/chainle_admin', headers=headers).get_data(as_text=True)
    assert f'Solver version <b>{now}</b>.' in html
    table = html.split('id="by-version"')[1].split('</table>')[0]
    assert f'{now} <span class="muted">(now)</span>' in table
    assert '0badc0de' in table and '812' in table and '2026-10-12 to 2026-10-13' in table
    assert f'Logged with solver {now}: its pick MAN,' in html
    assert 'Logged with solver 0badc0de: its pick LARGE,' in html


def test_admin_survives_a_database_error(client, tiny, monkeypatch):
    # The page stays up and says which half it couldn't read.
    _, headers = _admin(tiny, monkeypatch, fail='SELECT')
    resp = client.get('/chainle_admin', headers=headers)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Couldn't read the word lists" in html and "Couldn't read the round log" in html
    _, headers = _admin(tiny, monkeypatch, fail='INSERT')
    resp = client.post('/chainle_admin/log_round', data=_round_form(), headers=headers)
    assert resp.status_code == 302
    assert 'Database+error' in resp.headers['Location']
    assert 'tab=log' in resp.headers['Location']


def test_the_round_log_still_works_without_the_crowd_tables(client, tiny, monkeypatch):
    # Prod may run this code before mysql_tables/chainle_crowd.sql.
    _, headers = _admin(tiny, monkeypatch, fail='chainle_invalid_words')
    html = client.get('/chainle_admin?tab=log', headers=headers).get_data(as_text=True)
    assert 'Has mysql_tables/chainle_crowd.sql been run?' in html
    assert 'class="round-card"' in html and 'SNOW + FOOT' in html
    assert 'id="log-tab" class="tab-content active"' in html


# --- players' reports and the admin's word lists ----------------------------------------

def _offered_words(client, clues, kind='bridge'):
    return [s['word'] for s in _solve(client, clues, kind).get_json()['suggestions']]


def test_removed_words_are_never_offered_and_allowed_ones_reach_the_solver(client, tiny, monkeypatch):
    import routes.chainle as rc
    tiny(rows={'SELECT word FROM chainle_invalid_words': [('MAN',)],
               'SELECT word FROM chainle_added_words': [('hospitals',)]})
    assert 'MAN' not in _offered_words(client, ['snow', 'foot'])
    # What allowing does to the ranking is tested in test_chainle_solver.py.
    seen = {}
    solve = rc.ch.solve
    monkeypatch.setattr(rc.ch, 'solve', lambda *a, **kw: seen.update(kw) or solve(*a, **kw))
    _solve(client, ['doctor', 'nurse'])
    assert seen['removed'] == {'man'} and seen['kept'] == {'hospitals'}


def test_the_corrections_are_read_once_until_a_change(client, tiny):
    import routes.chainle as rc
    db = tiny()
    _solve(client, ['snow', 'foot'])
    _solve(client, ['big'], 'meaning')
    assert len(db.statements('SELECT word FROM chainle_invalid_words')) == 1
    rc.CHAINLE_CROWD.on_change()
    _solve(client, ['snow', 'foot'])
    assert len(db.statements('SELECT word FROM chainle_invalid_words')) == 2


def test_the_solver_works_without_the_corrections(client, tiny):
    # A database blip, or the tables not created yet: solve without them.
    tiny(fail='chainle_invalid_words')
    resp = _solve(client, ['snow', 'foot'])
    assert resp.status_code == 200
    assert resp.get_json()['suggestions'][0]['word'] == 'MAN'


def _report(client, action, word, clues=('SNOW', 'FOOT'), kind='bridge'):
    return client.post('/chainle', json={'action': action, 'word': word, 'clues': list(clues), 'kind': kind})


def test_a_refusal_counts_for_a_word_the_round_offers(client, tiny):
    from test_blossom import VOTE_LOG_FIELDS
    # This player's votes today, their refusals today, and the word's players.
    db = tiny(answers={'WHERE voter_hash = %s AND created_at': (0,),
                       "WHERE voter_hash = %s AND vote = 'invalid'": (1,),
                       'FROM chainle_word_votes v WHERE v.word': (1,)})
    resp = _report(client, 'refuse', 'BALL')
    assert resp.status_code == 200 and resp.get_json() == {'status': 'success'}
    [(_sql, params)] = db.statements('INSERT INTO chainle_word_votes')
    assert params[:2] == ('ball', 'invalid')
    assert params[3] == 'foot:snow'                  # the clues, sorted
    [log] = [dict(zip(VOTE_LOG_FIELDS, p)) for _s, p in db.statements('INSERT INTO chainle_vote_log')]
    assert (log['event'], log['mode']) == ('vote', 'bridge')


def test_a_refusal_of_a_word_the_round_never_offered_is_ignored(client, tiny):
    db = tiny()
    assert 'FOOTBALL' not in _offered_words(client, ['snow', 'foot'])
    resp = _report(client, 'refuse', 'football')
    # The same reply as a counted one: players can't tell the difference.
    assert resp.status_code == 200 and resp.get_json() == {'status': 'success'}
    assert not db.statements('INSERT INTO chainle_word_votes')


def test_taking_a_refusal_back(client, tiny):
    db = tiny()
    resp = _report(client, 'unrefuse', 'BALL')
    assert resp.get_json() == {'status': 'success'}
    [(sql, params)] = db.statements('DELETE FROM chainle_word_votes')
    assert params[:2] == ('ball', 'invalid')


@pytest.mark.parametrize('payload', [
    {'action': 'delete', 'word': 'ball', 'clues': ['snow', 'foot']},
    {'action': 'refuse', 'word': 'two words', 'clues': ['snow', 'foot']},
    {'action': 'refuse', 'word': 7, 'clues': ['snow', 'foot']},
    {'action': 'refuse', 'word': 'ball', 'clues': ['snow']},
], ids=['unknown-action', 'two-words', 'not-text', 'bad-clues'])
def test_a_junk_report_is_a_400(client, tiny, payload):
    db = tiny()
    assert client.post('/chainle', json=payload).status_code == 400
    assert not db.statements('chainle_word_votes')


def test_players_are_never_told_the_vote_counts(client, tiny):
    # As on /smush and /blossom: the page never says how many reports a
    # change takes, not in the FAQ, the toasts or a JS constant.
    html = client.get('/chainle').get_data(as_text=True)
    lowered = html.lower()
    for hint in ('crowd_remove_votes', 'different players', 'another player', 'players flag',
                 'players agreed', 'for everyone', '5 players', 'five players', 'three players'):
        assert hint not in lowered, f'page hints at the threshold: {hint!r}'
    assert 'take every report into account' in html
    assert 'data-refuse' in html and 'data-unrefuse' in html


REPORT = ('ball', 'invalid', 4, datetime(2026, 10, 21, 9, 30), 'waiting',
          datetime(2026, 10, 21, 8, 0), 'foot:snow', date(2026, 10, 21))


def test_admin_page_has_the_word_tabs_and_the_round_log(client, tiny, monkeypatch):
    db, headers = _admin(tiny, monkeypatch)
    db.rows['WITH board_dates'] = [REPORT]
    db.rows['FROM chainle_invalid_words ORDER BY'] = [('cats', datetime(2026, 10, 20, 7, 0), 'crowd')]
    html = client.get('/chainle_admin', headers=headers).get_data(as_text=True)
    for tab in ('Player Reports', 'Removed Words', 'Allowed Words', 'Round Log'):
        assert tab in html
    # Player Reports opens first, like /smush_admin.
    assert 'id="reports-tab" class="tab-content active"' in html
    reports = html.split('id="reports-tab"')[1].split('id="invalid-tab"')[0]
    assert '<strong>ball</strong>' in reports and 'FOOT<br>SNOW<br>' in reports
    assert 'value="invalid" class="btn btn-danger">Remove now' in reports
    assert 'value="missing" class="btn btn-success">Allow now' in reports
    assert '<strong>cats</strong>' in html.split('id="invalid-tab"')[1].split('id="added-tab"')[0]
    # The round log is all still there, on its own tab.
    assert 'id="round-form"' in html and 'class="round-card"' in html


def test_the_admin_removes_and_allows_words(client, tiny, monkeypatch):
    db, headers = _admin(tiny, monkeypatch)
    resp = client.post('/chainle_admin/add_word', headers=headers,
                       data={'word': 'Man', 'word_type': 'invalid', 'tab': 'invalid', 'q': 'ma'})
    assert resp.status_code == 302
    assert 'success=' in resp.headers['Location'] and 'tab=invalid' in resp.headers['Location']
    assert 'q=ma' in resp.headers['Location']
    assert db.statements('INSERT INTO chainle_invalid_words')
    client.post('/chainle_admin/add_word', headers=headers, data={'word': 'hospitals', 'word_type': 'missing'})
    assert db.statements('INSERT INTO chainle_added_words')
    client.post('/chainle_admin/remove_word', headers=headers, data={'word': 'man', 'word_type': 'invalid'})
    assert db.statements('DELETE FROM chainle_invalid_words WHERE word = %s')


@pytest.mark.parametrize('form,message', [
    ({'word': 'man'}, 'Choose+Remove+word+or+Allow+word+first'),
    ({'word': 'm4n', 'word_type': 'invalid'}, 'letters+only'),
    ({'word': 'zzzz', 'word_type': 'missing'}, 'solver'),
], ids=['no-choice', 'not-letters', 'unknown-word'])
def test_the_admin_word_form_refuses_what_it_cannot_use(client, tiny, monkeypatch, form, message):
    db, headers = _admin(tiny, monkeypatch)
    resp = client.post('/chainle_admin/add_word', headers=headers, data=form)
    assert 'error=' in resp.headers['Location'] and message in resp.headers['Location']
    assert not db.statements('INSERT INTO chainle_')


def test_the_word_routes_need_the_password(client, tiny):
    for path in ('/chainle_admin/add_word', '/chainle_admin/remove_word'):
        assert client.post(path, data={'word': 'man', 'word_type': 'invalid'}).status_code == 401
