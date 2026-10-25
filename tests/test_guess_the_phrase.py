"""/guess_the_phrase: the page, solving, players' added phrases and the admin
page (routes/guess_the_phrase.py, crowd.py).

Every database call goes through a scripted fake: secret_pass points at the
production JawsDB, so nothing here may reach a real connection. Most tests
run on a three-phrase list; the last few load the shipped files.
"""

import os
import time
from datetime import datetime, timedelta

import pytest
import pytz

from functions import phrase_solver as ps
from test_blossom import _admin_headers, _ScriptedDB

BOARD = "?OTEL CALIFORNIA"


def _bank(*rows):
    return ps.PhraseBank((d, sum(ps.TAG_BIT[t] for t in tags), pop) for d, tags, pop in rows)


class _Static:
    """A stand-in for the shipped files: three phrases, a few words."""

    def __init__(self):
        self.bank = _bank(('HOTEL CALIFORNIA', ['SONG'], 40),
                          ('HOTEL TRANSYLVANIA', ['MOVIE'], 38),
                          ('STAR WARS', ['MOVIE'], 45))
        self.words = ps.WordList([('HOTEL', 4.5), ('MOTEL', 4.6), ('CALIFORNIA', 5.0)])
        self.bigrams = ps.Bigrams([('HOTEL CALIFORNIA', 6.0)])
        self.blocklist = frozenset({ps.blocklist_hash('ZYZZYVA')})


@pytest.fixture
def gtp_db(monkeypatch):
    """The small phrase list, no cached corrections, and a scripted DB
    (empty unless a test installs answers)."""
    import crowd
    import routes.guess_the_phrase as g

    monkeypatch.setattr(g, '_static', {'data': _Static(), 'failed_at': 0.0})
    monkeypatch.setattr(g, '_overlay', {'value': None, 'expires': 0.0})

    def install(answers=None, rows=None, rowcount=1, fail=None):
        db = _ScriptedDB(answers, rows, rowcount, fail)
        monkeypatch.setattr(crowd, 'db_cursor', db)
        return db

    install()
    return install


@pytest.fixture
def no_list(monkeypatch):
    """The shipped files failed to load a moment ago."""
    import routes.guess_the_phrase as g
    monkeypatch.setattr(g, '_static', {'data': None, 'failed_at': time.time()})
    monkeypatch.setattr(g, '_overlay', {'value': None, 'expires': 0.0})


def _solve(client, board=BOARD, tried='', category='song'):
    return client.post('/guess_the_phrase', json={'board': board, 'tried': tried, 'category': category})


def _add(client, phrase, board=BOARD, tried='', category='song'):
    return client.post('/guess_the_phrase', json={
        'action': 'suggest_missing', 'phrase': phrase, 'board': board, 'tried': tried,
        'category': category})


# --- the page ---------------------------------------------------------------

def test_page_shows_the_list_and_every_category(client, gtp_db):
    html = client.get('/guess_the_phrase').get_data(as_text=True)
    # The hourly web probe expects this marker (datasets/health_web.yaml).
    assert 'id="gtp-bank-ok"' in html
    assert 'from 3 known phrases' in html
    for cid, _, _, _ in ps.CATEGORIES:
        assert f'value="{cid}"' in html


def test_faq_structured_data_parses_and_matches_the_visible_faq(client, gtp_db):
    # The FAQ is written twice: as JSON-LD for search engines and as the
    # visible section. A stray quote breaks the JSON; an edit to one copy
    # only makes them disagree.
    import json
    import re
    html = client.get('/guess_the_phrase').get_data(as_text=True)
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    faq = next(d for d in map(json.loads, blocks) if d.get('@type') == 'FAQPage')
    visible = re.sub(r'<[^>]+>', '', re.sub(r'<script.*?</script>', '', html, flags=re.S))
    visible = ' '.join(visible.split())
    for q in faq['mainEntity']:
        assert q['name'] in visible
        assert q['acceptedAnswer']['text'] in visible


def test_page_says_unofficial_and_credits_every_source(client, gtp_db):
    # The disclaimer and the credits the sources' licences ask for
    # (datasets/PHRASE_BANK.md has the reasons for each one).
    html = client.get('/guess_the_phrase').get_data(as_text=True)
    for required in (
        'Not affiliated with or endorsed by Bored Panda',
        'Wikidata', 'creativecommons.org/publicdomain/zero/1.0',
        'WordNet 3.0 Copyright 2006 by Princeton University',
        'Wiktionary contributors', 'creativecommons.org/licenses/by-sa/4.0',
        'wordfreq', 'Robyn Speer', 'SUBTLEX', 'Brysbaert', 'freely available', 'OpenSubtitles',
        'Google Books Ngrams', 'List of Dirty, Naughty, Obscene, and Otherwise Bad Words',
        'creativecommons.org/licenses/by/4.0',
    ):
        assert required in html, required
    assert 'wheel of fortune solver' not in html.lower()


def test_answers_are_listed_without_a_show_button(client, gtp_db):
    # The phrases show, masked, as soon as there's a board: no button opens them.
    html = client.get('/guess_the_phrase').get_data(as_text=True)
    assert 'id="gtp-answer-list"' in html
    assert 'gtp-answers-toggle' not in html and 'Show possible answers' not in html


def test_page_never_touches_the_database(client, gtp_db):
    db = gtp_db(fail='SELECT')
    assert client.get('/guess_the_phrase').status_code == 200
    assert not db.executed


def test_page_says_when_the_list_is_unavailable(client, no_list):
    resp = client.get('/guess_the_phrase')
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'id="gtp-bank-ok"' not in html
    assert 'unavailable right now' in html


def test_loading_the_list_gives_freed_memory_back(monkeypatch):
    # Parsing the files frees ~1M temporary strings; glibc keeps that memory
    # unless malloc_trim hands it back.
    import routes.guess_the_phrase as g
    calls = []
    monkeypatch.setattr(g, '_static', {'data': None, 'failed_at': 0.0})
    monkeypatch.setattr(g.ps, 'SolverData', lambda *paths: _Static())
    monkeypatch.setattr(g.ps, 'load_blocklist', lambda path: frozenset())
    monkeypatch.setattr(g, 'release_memory', lambda: calls.append(1))
    assert g._load_static() is not None
    g._load_static()
    assert calls == [1]   # once, on the load itself


# --- solving ----------------------------------------------------------------

def test_solve_returns_hints_for_the_board(client, gtp_db):
    data = _solve(client).get_json()
    assert data['total'] == 1
    assert data['blanks'] == 1
    assert data['phrases'] == [['HOTEL CALIFORNIA', 1.0]]
    assert data['advice'][0][0] == 'H'
    assert data['words'][0][:2] == ['MOTEL', 'HOTEL']
    assert data['guesses'] == []


def test_admin_and_player_changes_apply_to_solving(client, gtp_db):
    gtp_db(rows={'FROM gtp_invalid_words': [('HOTEL CALIFORNIA',)],
                 'FROM gtp_added_words': [('MOTEL CALIFORNIA',)]})
    data = _solve(client).get_json()
    assert [p for p, _ in data['phrases']] == ['MOTEL CALIFORNIA']


def test_solving_survives_a_database_outage(client, gtp_db):
    gtp_db(fail='gtp_invalid_words')
    resp = _solve(client)
    assert resp.status_code == 200
    assert resp.get_json()['total'] == 1


def test_nothing_known_fits_gives_word_by_word_guesses(client, gtp_db):
    data = _solve(client, tried='H').get_json()
    assert data['total'] == 0
    assert data['guesses'][0][0] == 'MOTEL CALIFORNIA'


@pytest.mark.parametrize('payload', [
    {'board': ''},
    {'board': 'AB1 ??'},
    {'board': '?' * 81},
    {'board': ' '.join(['?'] * 13)},
    {'board': '?' * 21},
    {'board': '???', 'tried': 'A1'},
    {'board': '???', 'tried': ['A']},
    {'board': '???', 'category': 'nope'},
    {'board': 7},
])
def test_junk_requests_are_a_400(client, gtp_db, payload):
    assert client.post('/guess_the_phrase', json=payload).status_code == 400


def test_a_non_json_body_is_a_415(client, gtp_db):
    resp = client.post('/guess_the_phrase', data='board=?', content_type='text/plain')
    assert resp.status_code == 415


def test_solving_without_the_list_is_a_503(client, no_list):
    resp = _solve(client)
    assert resp.status_code == 503
    assert 'unavailable' in resp.get_json()['error']


# --- players' added phrases -------------------------------------------------

def _votes(players, today=0):
    """Answers for the counts the vote path makes: this player's votes in
    the last day (the cap) and the phrase's players in the window."""
    return {
        'WHERE voter_hash = %s AND created_at': (today,),
        'FROM gtp_word_votes v WHERE v.word': (players,),
    }


def test_an_answer_that_doesnt_fit_the_board_is_not_recorded(client, gtp_db):
    db = gtp_db(_votes(1))
    assert _add(client, 'Star Wars').get_json() == {'status': 'success', 'result': 'not_on_board'}
    # M was tried and missed, so MOTEL can't be the answer.
    assert _add(client, 'Motel California', tried='M').get_json()['result'] == 'not_on_board'
    assert not db.statements('INSERT INTO gtp_word_votes')


def test_an_answer_already_listed_says_so(client, gtp_db):
    db = gtp_db(_votes(1))
    assert _add(client, 'hotel california').get_json()['result'] == 'already_listed'
    assert not db.statements('INSERT INTO gtp_word_votes')


def test_a_missing_answer_records_a_vote_for_this_puzzle(client, gtp_db):
    db = gtp_db(_votes(1))
    resp = _add(client, 'motel-california!')
    assert resp.get_json() == {'status': 'success', 'result': 'recorded'}
    [(_sql, params)] = db.statements('INSERT INTO gtp_word_votes')
    # The match key, and a dateless puzzle tag so old puzzles line up.
    assert params[:2] == ('MOTEL CALIFORNIA', 'missing')
    assert params[3] == 'song:5-10'
    assert not db.statements('INSERT INTO gtp_added_words')


def test_the_second_player_adds_it(client, gtp_db):
    import routes.guess_the_phrase as g
    db = gtp_db(_votes(2))
    _solve(client)                      # caches the corrections
    assert g._overlay['expires'] > 0
    _add(client, 'MOTEL CALIFORNIA')
    [(_sql, params)] = db.statements('INSERT INTO gtp_added_words')
    assert params[0] == 'MOTEL CALIFORNIA'
    assert g._overlay['expires'] == 0.0  # the next solve re-reads them


def test_votes_count_for_30_days(client, gtp_db):
    db = gtp_db(_votes(1))
    _add(client, 'MOTEL CALIFORNIA')
    [(_sql, params)] = db.statements('FROM gtp_word_votes v WHERE v.word')
    since = datetime.strptime(params[2], '%Y-%m-%d %H:%M:%S')
    expected = datetime.now(pytz.timezone('America/Los_Angeles')).replace(tzinfo=None) - timedelta(days=30)
    assert abs(since - expected) < timedelta(minutes=5)


def test_a_blocked_phrase_gets_the_same_reply_but_no_vote(client, gtp_db):
    db = gtp_db(_votes(1))
    assert _add(client, 'zyzzyva', board='???????').get_json()['result'] == 'recorded'
    assert not db.statements('INSERT INTO gtp_word_votes')


@pytest.mark.parametrize('payload', [
    {'action': 'reject', 'phrase': 'HOTEL CALIFORNIA', 'board': BOARD},
    {'action': 'suggest_missing', 'board': BOARD},
    {'action': 'suggest_missing', 'phrase': '123', 'board': BOARD},
    {'action': 'suggest_missing', 'phrase': 'HOTEL CALIFORNIA'},
])
def test_malformed_votes_are_a_400_and_touch_nothing(client, gtp_db, payload):
    db = gtp_db(_votes(1))
    assert client.post('/guess_the_phrase', json=payload).status_code == 400
    assert not db.statements('INSERT')


def test_the_digest_uses_the_same_window():
    # The digest can't import the routes, so it repeats the number.
    import routes.guess_the_phrase as g
    from monitoring import daily_digest
    assert daily_digest.GTP_WINDOW_DAYS == g.GTP_WINDOW_DAYS == g.GTP_CROWD.window_days


# --- admin --------------------------------------------------------------------

def test_admin_needs_the_password(client, gtp_db):
    assert client.get('/guess_the_phrase_admin').status_code == 401
    assert client.post('/guess_the_phrase_admin/add_word', data={'word': 'X'}).status_code == 401
    assert client.post('/guess_the_phrase_admin/remove_word', data={'word': 'X'}).status_code == 401


def test_admin_page_shows_lists_and_reports(client, gtp_db, monkeypatch):
    when = datetime(2026, 10, 11, 8, 0)
    gtp_db(rows={
        'FROM gtp_invalid_words': [('STAR WARS', when, 'admin')],
        'FROM gtp_word_votes v': [('MOTEL CALIFORNIA', 'missing', 1, when, 'waiting', when,
                                   'song:5-10', when.date())],
    })
    resp = client.get('/guess_the_phrase_admin', headers=_admin_headers(monkeypatch))
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'STAR WARS' in html and 'MOTEL CALIFORNIA' in html and 'song:5-10' in html
    assert 'last 30 days' in html
    assert '<td class="report-missing">missing</td>' in html


def test_admin_remove_takes_a_phrase_out_by_its_key(client, gtp_db, monkeypatch):
    db = gtp_db()
    resp = client.post('/guess_the_phrase_admin/add_word', headers=_admin_headers(monkeypatch),
                       data={'word': "Don't Stop", 'word_type': 'invalid'})
    assert resp.status_code == 302
    [(_sql, params)] = db.statements('INSERT INTO gtp_invalid_words')
    assert params == ('DONT STOP',)


def test_admin_without_a_choice_changes_nothing(client, gtp_db, monkeypatch):
    db = gtp_db()
    resp = client.post('/guess_the_phrase_admin/add_word', headers=_admin_headers(monkeypatch),
                       data={'word': 'STAR WARS'})
    assert 'error=' in resp.headers['Location']
    assert not db.statements('INSERT')


def _admin_text(client, monkeypatch, query):
    """The admin page for a search, as text with whitespace collapsed."""
    resp = client.get('/guess_the_phrase_admin', query_string={'q': query},
                      headers=_admin_headers(monkeypatch))
    assert resp.status_code == 200
    return ' '.join(resp.get_data(as_text=True).split())


def test_admin_search_says_a_shipped_phrase_is_listed(client, gtp_db, monkeypatch):
    gtp_db()
    text = _admin_text(client, monkeypatch, 'Hotel California')
    assert ("<strong>HOTEL CALIFORNIA</strong> is in the solver's list: "
            "shipped, tagged song, popularity 40.0.") in text
    assert 'Add it' not in text


def test_admin_search_offers_to_add_an_unlisted_phrase(client, gtp_db, monkeypatch):
    gtp_db()
    text = _admin_text(client, monkeypatch, "Motel California!")
    assert "<strong>MOTEL CALIFORNIA</strong> is not in the solver's list." in text
    assert '<input type="hidden" name="word" value="MOTEL CALIFORNIA">' in text
    # The Add button carries the search, so the page comes back to it.
    assert '<input type="hidden" name="q" value="Motel California!">' in text


def test_admin_search_shows_added_and_removed_phrases(client, gtp_db, monkeypatch):
    gtp_db(rows={'SELECT word FROM gtp_invalid_words': [('STAR WARS',)],
                 'SELECT word FROM gtp_added_words': [('MOTEL CALIFORNIA',)]})
    text = _admin_text(client, monkeypatch, 'motel california')
    assert "<strong>MOTEL CALIFORNIA</strong> is in the solver's list: added (see Added Phrases)." in text
    text = _admin_text(client, monkeypatch, 'star wars')
    assert "<strong>STAR WARS</strong> is removed from the shipped list, so players don't see it." in text


def test_admin_search_for_a_board_or_nothing_says_nothing(client, gtp_db, monkeypatch):
    gtp_db()
    for query in ('song:', 'song:5-10', '', '123'):
        assert 'class="phrase-status"' not in _admin_text(client, monkeypatch, query)


def test_admin_add_refuses_a_shipped_phrase(client, gtp_db, monkeypatch):
    # Not a harmless duplicate: an added copy scores ADDED_POP in every
    # category, not just the ones the phrase is tagged for.
    from urllib.parse import parse_qs, urlsplit
    db = gtp_db()
    resp = client.post('/guess_the_phrase_admin/add_word', headers=_admin_headers(monkeypatch),
                       data={'word': 'Hotel California', 'word_type': 'missing', 'q': 'hotel'})
    args = parse_qs(urlsplit(resp.headers['Location']).query)
    assert args['error'] == ['HOTEL CALIFORNIA is already in the shipped phrase list - nothing changed']
    assert args['q'] == ['hotel']
    assert not db.statements('INSERT')


def test_admin_add_takes_an_unlisted_or_removed_phrase(client, gtp_db, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    db = gtp_db(rows={'SELECT word FROM gtp_invalid_words': [('STAR WARS',)]})
    for phrase in ('Motel California', 'Star Wars'):
        resp = client.post('/guess_the_phrase_admin/add_word', headers=_admin_headers(monkeypatch),
                           data={'word': phrase, 'word_type': 'missing', 'q': 'x', 'tab': 'added'})
        args = parse_qs(urlsplit(resp.headers['Location']).query)
        assert args == {'q': ['x'], 'tab': ['added'],
                        'success': [f'{phrase.upper()} added to the phrase list']}
    assert [p for _sql, p in db.statements('INSERT INTO gtp_added_words')] == [
        ('MOTEL CALIFORNIA',), ('STAR WARS',)]


# --- the shipped files ------------------------------------------------------

@pytest.fixture(scope='module')
def shipped():
    from data import data_folder
    data = ps.SolverData(os.path.join(data_folder, 'phrase_bank.tsv.gz'),
                         os.path.join(data_folder, 'phrase_words.tsv.gz'),
                         os.path.join(data_folder, 'phrase_bigrams.tsv.gz'))
    data.blocklist = ps.load_blocklist(os.path.join(data_folder, 'phrase_blocklist.txt'))
    return data


def test_the_shipped_list_finds_a_famous_phrase(shipped):
    assert shipped.bank.size > 600_000
    res = ps.solve(shipped, 'THE ?O??ATHER', tags=('MOVIE',), mashup='no')
    assert res['candidates'][0][0] == 'THE GODFATHER'


def test_the_shipped_blocklist_loads(shipped):
    assert len(shipped.blocklist) > 300
    assert all(len(h) == 16 for h in shipped.blocklist)


def test_every_category_uses_real_tags():
    ids = [c[0] for c in ps.CATEGORIES]
    assert len(ids) == len(set(ids))
    for _, _, tags, mashup in ps.CATEGORIES:
        assert set(tags) <= set(ps.TAGS)
        assert mashup in ('no', 'also', 'only')
