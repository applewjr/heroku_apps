"""/chainle: the page, solving, and /chainle_admin's word lists
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
    # Chainle's own words, that is: its title, and the page from its heading
    # to the solver links. The menu and those links tag other pages Beta
    # (helpers.BETA_PAGES).
    html = client.get('/chainle').get_data(as_text=True)
    own = re.search(r'<title>.*?</title>', html, re.S).group(0)
    own += html[html.index('<h1'):html.index('class="jj-more"')]
    assert not re.search(r'\bbeta\b', own, re.I)


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


# --- the admin page ----------------------------------------------------------------

def _admin(tiny, monkeypatch, fail=None):
    """The admin login and a scripted database."""
    return tiny(fail=fail), _admin_headers(monkeypatch)


def test_admin_needs_the_password(client, tiny):
    assert client.get('/chainle_admin').status_code == 401


def test_the_round_log_is_gone(client, tiny, monkeypatch):
    # James, 2026-11-22: no round logged since 2026-10-17, and re-scoring
    # the newest 100 on every load made the page slow. The page never reads
    # chainle_rounds or runs the solver on a logged round now.
    db, headers = _admin(tiny, monkeypatch)
    scored = []
    monkeypatch.setattr(ch, 'assess', lambda *args, **kwargs: scored.append(args))
    html = client.get('/chainle_admin?tab=log', headers=headers).get_data(as_text=True)
    assert 'Round Log' not in html and 'id="log-tab"' not in html and 'Rounds logged' not in html
    assert html.count('class="tab-content active"') == 1
    assert scored == [] and not db.statements('chainle_rounds')
    for path in ('/chainle_admin/log_round', '/chainle_admin/delete_round'):
        assert client.post(path, data={'id': '1'}, headers=headers).status_code in (404, 405)


# solver_version() still labels what 4x3 logs (routes/four_by_three.py).
def test_the_solver_version_is_a_short_code_that_follows_the_data(tiny, monkeypatch):
    import routes.chainle as rc
    version = rc.solver_version()
    assert re.fullmatch(r'[0-9a-f]{8}', version)
    assert rc.solver_version() == version
    other = ch.pack(_V, zipf={w: 4.0 for w in _V}, answer=set(_V))
    monkeypatch.setattr(rc, '_static', {'data': ch.Data(**other), 'failed_at': 0.0})
    assert rc.solver_version() != version


def test_admin_survives_a_database_error(client, tiny, monkeypatch):
    # The page stays up and says which part it couldn't read.
    _, headers = _admin(tiny, monkeypatch, fail='SELECT')
    resp = client.get('/chainle_admin', headers=headers)
    assert resp.status_code == 200
    assert "Couldn't read the word lists" in resp.get_data(as_text=True)


# --- the admin's word lists -----------------------------------------------------------

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


# --- players' reports: the "Chainle refused a word?" box ---------------------------
# James asked, 2026-10-25: a box under the answers, starting with the last
# word the player copied, voting the word off as Smush's ✗ does.

def _report(client, word, clues=('SNOW', 'FOOT'), kind='bridge', action='refuse'):
    return client.post('/chainle', json={'action': action, 'word': word, 'clues': list(clues), 'kind': kind})


def _voting_db(tiny):
    """A database that takes a vote: this player's votes today, their
    refusals today, and the word's players."""
    return tiny(answers={'WHERE voter_hash = %s AND created_at': (0,),
                         "WHERE voter_hash = %s AND vote = 'invalid'": (1,),
                         'FROM chainle_word_votes v WHERE v.word': (1,)})


def test_the_page_has_the_refused_word_box(client, tiny):
    html = client.get('/chainle').get_data(as_text=True)
    for hook in ('id="chn-report"', 'id="chn-report-input"', 'id="chn-report-btn"',
                 'Chainle refused a word?', "action: 'refuse'", 'What if Chainle refuses a word?',
                 'The last word you copied fills in here'):
        assert hook in html, f'chainle.html lost {hook}'
    # Under the answers, inside the results, so it only shows with them.
    results = html.split('id="chn-results"')[1].split('Play Chainle')[0]
    assert results.index('id="chn-answers"') < results.index('id="chn-report"')


def test_a_report_counts_for_a_word_the_round_offers(client, tiny):
    import crowd
    db = _voting_db(tiny)
    assert 'BALL' in _offered_words(client, ['snow', 'foot'])
    resp = _report(client, 'BALL')
    assert resp.status_code == 200 and resp.get_json() == {'status': 'success', 'result': 'recorded'}
    [(_sql, params)] = db.statements('INSERT INTO chainle_word_votes')
    assert params[:2] == ('ball', 'invalid')
    assert params[3] == 'foot:snow'                  # the clues, sorted
    [log] = db.log_rows('chainle')
    assert (log['event'], log['mode']) == ('vote', 'bridge')
    # Fewer players for a rarer word, as on /smush.
    assert log['needed'] == crowd.remove_votes_needed('ball')


def test_a_word_the_round_does_not_offer_is_not_counted(client, tiny):
    db = _voting_db(tiny)
    assert 'FOOTBALL' not in _offered_words(client, ['snow', 'foot'])
    resp = _report(client, 'football')
    assert resp.status_code == 200
    assert resp.get_json() == {'status': 'success', 'result': 'not_suggested'}
    assert not db.statements('INSERT INTO chainle_word_votes')


def test_a_word_already_taken_off_gets_the_usual_reply(client, tiny):
    # A player whose list was loaded before the word went: thanks, no vote.
    db = tiny(rows={'SELECT word FROM chainle_invalid_words': [('ball',)]})
    resp = _report(client, 'BALL')
    assert resp.get_json() == {'status': 'success', 'result': 'recorded'}
    assert not db.statements('INSERT INTO chainle_word_votes')


@pytest.mark.parametrize('payload', [
    {'action': 'unrefuse', 'word': 'ball', 'clues': ['snow', 'foot']},
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
    # change takes, not in the FAQ, the box's messages or a JS constant.
    html = client.get('/chainle').get_data(as_text=True)
    lowered = html.lower()
    for hint in ('crowd_remove_votes', 'different players', 'another player', 'players flag',
                 'players agreed', 'for everyone', '5 players', 'five players', 'three players'):
        assert hint not in lowered, f'page hints at the threshold: {hint!r}'
    assert 'take every report into account' in html
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    faq = next(d for d in map(json.loads, blocks) if d.get('@type') == 'FAQPage')
    assert 'take every report into account' in json.dumps(faq)


REPORT = ('ball', 'invalid', 4, datetime(2026, 10, 21, 9, 30), 'waiting',
          datetime(2026, 10, 21, 8, 0), 'foot:snow', date(2026, 10, 21))


def test_admin_page_has_the_word_tabs(client, tiny, monkeypatch):
    db, headers = _admin(tiny, monkeypatch)
    db.rows['WITH board_dates'] = [REPORT]
    db.rows['FROM chainle_invalid_words ORDER BY'] = [('cats', datetime(2026, 10, 20, 7, 0), 'crowd')]
    html = client.get('/chainle_admin?tab=reports', headers=headers).get_data(as_text=True)
    for tab in ("Today's Rounds", 'Player Reports', 'Removed Words', 'Allowed Words'):
        assert tab in html
    assert 'id="reports-tab" class="tab-content active"' in html
    reports = html.split('id="reports-tab"')[1].split('id="invalid-tab"')[0]
    assert 'with the box under the answers on /chainle' in reports
    assert '<strong>ball</strong>' in reports and 'FOOT<br>SNOW<br>' in reports
    assert 'value="invalid" class="btn btn-danger">Remove now' in reports
    assert 'value="missing" class="btn btn-success">Allow now' in reports
    assert '<strong>cats</strong>' in html.split('id="invalid-tab"')[1].split('id="added-tab"')[0]


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
