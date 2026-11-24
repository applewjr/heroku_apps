"""Chainle's confirmed #1 words (routes/chainle.py): James enters a round's best
word on /chainle_admin after playing it, and /chainle puts it at the top of
the list, marked Checked, for anyone who enters those clues.

Nothing here reaches a real database: the table is an in-memory stand-in, and
the SQL is checked against test_blossom's scripted fake. Clue words come from
the tiny test vocabulary (test_chainle_solver), never a real round.
"""

import json
import re
import threading
import time
from datetime import date
from urllib.parse import unquote_plus

import pytest

import routes.chainle as rc
from functions import chainle as ch
from test_blossom import _admin_headers, _ScriptedDB
from test_chainle import tiny  # noqa: F401
from test_chainle_boards import DAY, DAY_ROUNDS, MemoryRounds, R1, R4, R5, _text

BRIDGE = ['snow', 'foot']


@pytest.fixture
def words(tiny):
    """The tiny vocabulary itself, for the engine's tests."""
    return rc.get_data()


class MemoryBest:
    """Stands in for chainle_best: {(kind, clue_key): (word, day, top, rank)}."""

    def __init__(self):
        self.rows = {}
        self.reads = 0
        self.fail = False

    def fetch(self, sql, params):
        if 'FROM chainle_best' not in sql:
            raise AssertionError(sql)
        if self.fail:
            raise RuntimeError('database down')
        self.reads += 1
        if sql.startswith('SELECT kind, clue_key, word FROM'):
            return [(kind, key, row[0]) for (kind, key), row in self.rows.items()]
        return [(kind, key, row[0], row[3], row[2]) for (kind, key), row in self.rows.items() if key in params]

    def save(self, day, rounds):
        if self.fail:
            raise RuntimeError('database down')
        saved = cleared = first = 0
        for clues, word in rounds:
            key = (rc.kind_of(clues), rc.clue_key(rc.clue_text(clues)))
            if word is None:
                cleared += self.rows.pop(key, None) is not None
                continue
            top, rank = rc._solver_had(clues, key[0], word)
            self.rows[key] = (word, day, top, rank)
            saved += 1
            first += rank == 1
        return saved, cleared, first


@pytest.fixture
def best(monkeypatch):
    table = MemoryBest()
    monkeypatch.setattr(rc, '_fetch', table.fetch)
    monkeypatch.setattr(rc, 'save_best', table.save)
    monkeypatch.setattr(rc, '_best', {'value': None, 'expires': 0.0, 'gen': 0, 'reads': 0, 'read': 0})
    return table


@pytest.fixture
def rounds(monkeypatch):
    table = MemoryRounds()
    table.approve(DAY, 'chainle.io', {n: _text(c) for n, c in enumerate(DAY_ROUNDS, 1)})
    monkeypatch.setattr(rc, 'rows_on', lambda first, last: [r for r in table.rows.values() if first <= r[0] <= last])
    monkeypatch.setattr(rc, '_sets_cache', {})
    monkeypatch.setattr(rc, '_today', lambda: DAY)
    return table


def _solve(client, clues, kind='bridge'):
    resp = client.post('/chainle', json={'clues': clues, 'kind': kind})
    assert resp.status_code == 200
    return resp.get_json()['suggestions']


def _save(client, headers, rounds=((1, R1),), words=None, day=DAY):
    data = {'played_on': day.isoformat(), 'tab': 'rounds'}
    for n, clues in rounds:
        data.update({f'round{n}_{m}': w.upper() for m, w in enumerate(clues, 1)})
    data.update({f'best{n}': w for n, w in (words or {}).items()})
    resp = client.post('/chainle_admin/save_best', headers=headers, data=data)
    assert resp.status_code == 302
    return unquote_plus(resp.headers['Location'])


# --- the engine -----------------------------------------------------------------------

def _rows(data, clues=BRIDGE, kind='bridge', best=None, **kw):
    return ch.solve(data, clues, kind, best=best, **kw)['suggestions']


def test_the_confirmed_word_goes_first_and_the_rest_keep_their_order(words):
    plain = _rows(words)
    assert len(plain) > 2 and not any(r.get('checked') for r in plain)
    last = plain[-1]['word']
    rows = _rows(words, best=last)
    assert rows[0]['word'] == last and rows[0]['checked'] and rows[0]['strength'] == 1.0
    assert [r['word'] for r in rows[1:]] == [r['word'] for r in plain if r['word'] != last]
    assert [r['strength'] for r in rows[1:]] == [r['strength'] for r in plain if r['word'] != last]
    # Its own phrases come with it.
    assert rows[0]['phrases'] == plain[-1]['phrases'] and rows[0]['type'] == plain[-1]['type']


def test_already_first_it_only_gains_the_mark(words):
    plain = _rows(words)
    rows = _rows(words, best=plain[0]['word'].lower())
    assert [r['word'] for r in rows] == [r['word'] for r in plain] and rows[0]['checked']


def test_the_list_never_grows(words):
    plain = _rows(words, top=3)
    rows = _rows(words, top=3, best='ZEBRA')
    assert len(rows) == len(plain) == 3
    assert rows[0] == {'word': 'ZEBRA', 'type': 'meaning', 'phrases': [None, None], 'patterns': [None, None],
                       'strength': 1.0, 'checked': True}


def test_a_word_the_solver_does_not_know_works_in_the_closest_round_too(words):
    rows = _rows(words, clues=['big'], kind='meaning', best='ZEBRA')
    assert rows[0]['word'] == 'ZEBRA' and rows[0]['checked'] and rows[0]['phrases'] == []


def test_its_other_forms_leave_the_list(words):
    # FEET's base form is FOOT in the tiny data: confirmed, it takes FOOT's
    # place rather than standing beside it.
    plain = [r['word'] for r in _rows(words, clues=['snow', 'ball'])]
    assert words.id('feet') >= 0
    rows = [r['word'] for r in _rows(words, clues=['snow', 'ball'], best='FEET')]
    assert rows[0] == 'FEET' and 'FOOT' not in rows and len(rows) <= max(len(plain), 1)


def test_it_wins_over_the_removed_list(words):
    top = _rows(words)[0]['word']
    assert _rows(words, removed=[top.lower()])[0]['word'] != top
    assert _rows(words, removed=[top.lower()], best=top)[0]['word'] == top


def test_an_unknown_clue_still_says_so(words):
    result = ch.solve(words, ['snow', 'qqqq'], 'bridge', best='BALL')
    assert result['unknown'] == ['QQQQ'] and result['suggestions'] == []


# --- the page -------------------------------------------------------------------------

def test_players_get_it_for_those_clues_in_any_order_on_any_day(client, tiny, best):
    plain = _solve(client, BRIDGE)
    word = plain[-1]['word']
    best.rows[('bridge', 'FOOT SNOW')] = (word, date(2026, 1, 1), None, None)
    rc._forget_best()
    for clues in (['snow', 'foot'], ['FOOT', 'Snow']):
        rows = _solve(client, clues)
        assert rows[0]['word'] == word and rows[0]['checked']
    # Other clues, and the same word as a closest-meaning round, are untouched.
    assert not any(r.get('checked') for r in _solve(client, ['doctor', 'nurse']))
    assert not any(r.get('checked') for r in _solve(client, ['snow'], 'meaning'))


def test_the_words_are_read_once_and_again_after_a_save(client, tiny, best):
    _solve(client, BRIDGE)
    _solve(client, ['doctor', 'nurse'])
    assert best.reads == 1
    rc._forget_best()
    _solve(client, BRIDGE)
    assert best.reads == 2


def test_a_failed_read_keeps_the_last_words_and_waits(client, tiny, best, monkeypatch):
    alerts = []
    monkeypatch.setattr(rc.alerts, 'alert_throttled', lambda name, **kw: alerts.append(name))
    best.fail = True
    assert _solve(client, BRIDGE)            # no words yet: the list as it was
    assert alerts == ['chainle_best_read_failed']
    _solve(client, BRIDGE)                   # not tried again straight away
    assert alerts == ['chainle_best_read_failed']
    best.fail = False
    best.rows[('bridge', 'FOOT SNOW')] = ('BALL', DAY, None, None)
    rc._forget_best()
    assert _solve(client, BRIDGE)[0]['checked']
    best.fail = True
    rc._forget_best()
    assert _solve(client, BRIDGE)[0]['checked']


def test_a_save_during_a_read_is_not_lost(best, monkeypatch):
    # The read started before the save, so its words are used once and not kept.
    def slow(sql, params):
        rc._forget_best()
        return []
    monkeypatch.setattr(rc, '_fetch', slow)
    assert rc.get_best() == {}
    assert rc._best['value'] is None and rc._best['expires'] <= time.monotonic()


WORDS = {('bridge', 'FOOT SNOW'): 'SHOE'}


def _overlapping(monkeypatch, slow_outcome, meanwhile=lambda: None):
    """Two reads of the words at once: the first to start finishes last, as
    slow_outcome does. `meanwhile` runs once it has started. Returns what
    each request was given."""
    monkeypatch.setattr(rc.alerts, 'alert_throttled', lambda *a, **kw: None)
    started, go, given, calls = threading.Event(), threading.Event(), {}, []

    def fetch(sql, params):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            assert go.wait(10)
            return slow_outcome()
        return [key + (word,) for key, word in WORDS.items()]
    monkeypatch.setattr(rc, '_fetch', fetch)
    thread = threading.Thread(target=lambda: given.update(slow=rc.get_best()))
    thread.start()
    assert started.wait(10)
    meanwhile()
    given['fast'] = rc.get_best()
    go.set()
    thread.join(10)
    return given


def _down():
    raise RuntimeError('database down')


def test_a_failed_read_never_wipes_another_requests_words(best, monkeypatch):
    given = _overlapping(monkeypatch, _down)
    assert given == {'fast': WORDS, 'slow': WORDS}
    assert rc.get_best() == WORDS
    assert rc._best['expires'] > time.monotonic() + rc.BEST_RETRY_SECONDS      # and isn't cut short


def test_the_same_once_the_words_are_known_and_a_refresh_is_slow(best, monkeypatch):
    # A refresh slower than the 15 seconds it keeps the others waiting.
    rc._best.update(value={('meaning', 'BIG'): 'HUGE'}, expires=0.0)
    given = _overlapping(monkeypatch, _down, meanwhile=lambda: rc._best.update(expires=0.0))
    assert given == {'fast': WORDS, 'slow': WORDS} and rc.get_best() == WORDS


def test_an_older_read_that_lands_late_is_not_kept(best, monkeypatch):
    given = _overlapping(monkeypatch, lambda: [])
    assert given == {'fast': WORDS, 'slow': {}} and rc.get_best() == WORDS


def test_a_read_that_works_is_kept_after_one_that_failed(best, monkeypatch):
    monkeypatch.setattr(rc.alerts, 'alert_throttled', lambda *a, **kw: None)
    calls = []

    def fetch(sql, params):
        calls.append(1)
        if len(calls) == 1:
            assert rc.get_best() == {}                 # a second request comes and fails
            return [key + (word,) for key, word in WORDS.items()]
        raise RuntimeError('database down')
    monkeypatch.setattr(rc, '_fetch', fetch)
    assert rc.get_best() == WORDS and rc.get_best() == WORDS and len(calls) == 2


def test_a_refusal_report_never_votes_a_confirmed_word_off(client, tiny, best, monkeypatch):
    votes = []
    monkeypatch.setattr(rc.CHAINLE_CROWD, 'record_vote', lambda *a: votes.append(a))
    monkeypatch.setattr(rc, 'get_corrections', lambda: (frozenset(), frozenset()))
    plain = _solve(client, BRIDGE)
    word, other = plain[0]['word'], plain[1]['word']
    best.rows[('bridge', 'FOOT SNOW')] = (word, DAY, None, None)
    rc._forget_best()

    def report(w):
        return client.post('/chainle', json={'action': 'refuse', 'word': w, 'clues': BRIDGE, 'kind': 'bridge'}).get_json()
    assert report(word) == {'status': 'success', 'result': 'recorded'} and votes == []
    assert report(other) == {'status': 'success', 'result': 'recorded'} and len(votes) == 1


def test_the_page_marks_a_confirmed_word_and_says_what_it_means(client):
    html = client.get('/chainle').get_data(as_text=True)
    assert "const CHECKED = '<span class=\"chn-checked\"" in html
    assert "(s.checked ? CHECKED : '<span class=\"chn-bar\"" in html
    said = 'A word marked Checked is the exception: it was entered by hand, by someone who had played the round.'
    # The visible FAQ, its structured data, and the sources note.
    assert html.count(said) == 3
    blocks = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    faq = next(b for b in blocks if b.get('@type') == 'FAQPage')
    assert any(said in q['acceptedAnswer']['text'] for q in faq['mainEntity'])


def test_the_public_page_never_names_who_enters_them(client):
    html = client.get('/chainle').get_data(as_text=True)
    assert 'chainle_admin' not in html and 'save_best' not in html


# --- /chainle_admin -------------------------------------------------------------------

def test_saving_needs_the_password(client):
    assert client.post('/chainle_admin/save_best', data={}).status_code == 401


def test_a_sets_words_are_saved_together(client, tiny, best, rounds, monkeypatch):
    headers = _admin_headers(monkeypatch)
    top = _solve(client, R1)[0]['word']
    to = _save(client, headers, [(1, R1), (4, R4), (5, R5)], {1: top.lower(), 4: 'zebra', 5: ''})
    assert f'Saved 2 #1 words for {DAY:%m-%d} (the solver had 1 of them first)' in to and 'tab=rounds' in to
    assert best.rows[('bridge', 'FOOT SNOW')][0] == top and best.rows[('bridge', 'FOOT SNOW')][3] == 1
    key = ('bridge', rc.clue_key(_text(R4)))
    assert best.rows[key][0] == 'ZEBRA' and best.rows[key][3] is None
    # Live straight away, with no approval needed.
    assert _solve(client, R4)[0] == dict(_solve(client, R4)[0], word='ZEBRA', checked=True)


def test_the_tab_shows_the_words_and_where_the_solver_had_them(client, tiny, best, rounds, monkeypatch):
    headers = _admin_headers(monkeypatch)
    top = _solve(client, R1)[0]['word']
    _save(client, headers, [(1, R1), (4, R4)], {1: top, 4: 'zebra'})
    html = client.get('/chainle_admin', headers=headers).get_data(as_text=True)
    assert html.count('placeholder="#1 word"') == 5
    assert f'value="{top}"' in html and 'solver had it #1' in html
    assert 'value="ZEBRA"' in html and "solver didn't have it" in html
    # Each box belongs to its own card's form.
    form = re.search(r'id="(best-[^"]+)"', html).group(1)
    assert html.count(f'form="{form}"') == 5


def test_an_empty_box_takes_the_word_back(client, tiny, best, rounds, monkeypatch):
    headers = _admin_headers(monkeypatch)
    _save(client, headers, words={1: 'zebra'})
    assert _solve(client, R1)[0]['word'] == 'ZEBRA'
    assert 'took 1 back' in _save(client, headers, words={1: ' '})
    assert best.rows == {} and not _solve(client, R1)[0].get('checked')


@pytest.mark.parametrize('word, said', [('snow', 'SNOW is one of its clues'), ('two words', 'letters only'),
                                        ('it3', 'letters only'), ('a' * 41, 'letters only'), ('<b>', 'letters only')])
def test_a_bad_word_saves_nothing(client, tiny, best, rounds, monkeypatch, word, said):
    to = _save(client, _admin_headers(monkeypatch), [(1, R1), (4, R4)], {1: word, 4: 'zebra'})
    assert 'error=' in to and said in to and best.rows == {}


def test_forms_that_are_not_a_set_are_refused(client, tiny, best, rounds, monkeypatch):
    headers = _admin_headers(monkeypatch)
    assert 'Not a set' in _save(client, headers, rounds=(), words={1: 'zebra'})
    assert 'error=' in _save(client, headers, rounds=[(1, ['sn0w', 'foot'])], words={1: 'zebra'})
    assert 'Type at least one' in _save(client, headers)
    resp = client.post('/chainle_admin/save_best', headers=headers, data={'played_on': 'x'})
    assert 'Not a date' in unquote_plus(resp.headers['Location'])
    best.fail = True
    assert 'Database error' in _save(client, headers, words={1: 'zebra'})


def test_the_tab_still_shows_if_the_words_cannot_be_read(client, tiny, best, rounds, monkeypatch):
    # With no Save: five empty boxes here don't mean there are no words, and
    # saving them would take every word back.
    headers = _admin_headers(monkeypatch)
    _save(client, headers, words={1: 'zebra'})
    best.fail = True
    html = client.get('/chainle_admin', headers=headers).get_data(as_text=True)
    assert _text(R1).replace(' ', ' + ') in html
    assert 'Has mysql_tables/chainle_best.sql been run?' in html
    assert html.count('placeholder="not loaded"') == 5 and html.count('aria-label="Round 1, the #1 word" disabled>') == 1
    assert '<button type="submit" class="btn btn-primary" disabled>Save #1 words</button>' in html
    # Readable again: the boxes, the word and Save are back.
    best.fail = False
    html = client.get('/chainle_admin', headers=headers).get_data(as_text=True)
    assert html.count('placeholder="#1 word"') == 5 and 'value="ZEBRA"' in html
    assert '<button type="submit" class="btn btn-primary">Save #1 words</button>' in html
    assert 'Has mysql_tables/chainle_best.sql been run?' not in html


# --- the SQL --------------------------------------------------------------------------

def test_saving_is_one_transaction(client, tiny, monkeypatch):
    db = _ScriptedDB()
    monkeypatch.setattr(rc, 'db_cursor', db)
    rc.save_best(DAY, [(R1, 'BALL'), (R5, None)])
    sql = [s for s, _p in db.executed]
    assert sql[0] == 'START TRANSACTION'
    assert 'INSERT INTO chainle_best' in sql[1] and 'ON DUPLICATE KEY UPDATE' in sql[1]
    assert sql[2].startswith('DELETE FROM chainle_best WHERE kind = %s AND clue_key = %s')
    insert = db.executed[1][1]
    assert insert[:4] == ('bridge', 'FOOT SNOW', 'BALL', DAY)
    assert db.executed[2][1] == ('meaning', rc.clue_key(_text(R5)))


def test_a_failed_write_rolls_the_rest_back(tiny, monkeypatch):
    db = _ScriptedDB(fail='DELETE FROM chainle_best')
    monkeypatch.setattr(rc, 'db_cursor', db)
    with pytest.raises(RuntimeError):
        rc.save_best(DAY, [(R1, 'BALL'), (R5, None)])
    assert [sql.split(' ')[0] for sql, _p in db.executed] == ['START', 'INSERT', 'DELETE', 'ROLLBACK']
    assert db.commits == 0


def test_the_table_fits_what_is_stored():
    sql = open('mysql_tables/chainle_best.sql', encoding='utf-8').read()
    widths = {name: int(n) for name, n in re.findall(r'^\s+(\w+)\s+(?:VAR)?CHAR\((\d+)\)', sql, re.M)}
    assert widths['word'] >= rc.MAX_WORD_LEN and widths['clue_key'] >= 3 * rc.MAX_CLUE_LEN + 2
    assert widths['solver_version'] >= 8 and widths['kind'] >= len('meaning')
    assert 'UNIQUE KEY uq_round (kind, clue_key)' in sql
