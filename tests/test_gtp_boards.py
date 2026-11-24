"""Guess The Phrase's today's board (routes/guess_the_phrase.py): the word
lengths early players tap in, offered to the players after them, and the
Today's Board tab of /guess_the_phrase_admin.

Nothing here reaches a real database: secret_pass points at the production
JawsDB. Most tests swap the module's table functions for an in-memory
stand-in (MemoryBoards); the SQL itself is checked against test_blossom's
scripted fake. Each simulated player is a different X-Forwarded-For, which is
what helpers.client_ip reads. The clock is the game's (Europe/Vilnius), set
through routes.guess_the_phrase._game_now. Boards are made-up word lengths,
never a real puzzle's.
"""

import json
import re
import threading
from datetime import date, datetime, timedelta
from urllib.parse import unquote_plus

import pytest
import pytz

import routes.guess_the_phrase as gtp
from test_blossom import _admin_headers, _ScriptedDB

VILNIUS = pytz.timezone('Europe/Vilnius')
DAY = date(2026, 11, 23)
# Midday on the 24th in Vilnius: the 23rd's puzzle went live at 23:30 the
# night before, and the 24th's comes at 23:30 tonight.
NOON = VILNIUS.localize(datetime(2026, 11, 24, 12, 0))
BOARD, OTHER = [4, 2, 5, 3], [6, 6]
IPS = [f'203.0.113.{n}' for n in range(1, 40)]
ONE_NETWORK = ['2001:db8:1:1::1', '2001:db8:1:2::1', '2001:db8:1:3::1']
FAQ_Q = "Do I have to enter today's board?"


class MemoryBoards:
    """Stands in for gtp_boards, rows in routes.guess_the_phrase._COLUMNS
    order. One row per (shape, voter, day), as the table's unique key keeps
    it."""

    def __init__(self):
        self.rows = {}
        self.clock = datetime(2026, 11, 24, 2, 0)
        self.reads = 0

    def store(self, day, voter, prefix, shape, category, source):
        self.clock += timedelta(minutes=1)
        old = self.rows.get((shape, voter, day))
        created = old[6] if old else self.clock
        self.rows[(shape, voter, day)] = (shape, category, source, voter, prefix, day, created, self.clock)

    def approve(self, day, shape, category):
        replaced = self._delete(lambda r: not (r[5] == day and r[2] == gtp.APPROVAL and r[0] != shape))
        self.store(day, gtp.APPROVAL, None, shape, category, gtp.APPROVAL)
        return replaced

    def _delete(self, keep):
        gone = [key for key, row in self.rows.items() if not keep(row)]
        for key in gone:
            del self.rows[key]
        return len(gone)

    def unapprove(self, day):
        return self._delete(lambda r: not (r[5] == day and r[2] == gtp.APPROVAL))

    def remove(self, shape, day):
        return self._delete(lambda r: not (r[0] == shape and r[5] == day and r[2] != gtp.APPROVAL))

    def between(self, first, last):
        self.reads += 1
        return [row for row in self.rows.values() if first <= row[5] <= last]


@pytest.fixture
def boards(monkeypatch):
    """The in-memory table, live, at midday in Vilnius with DAY's puzzle on,
    and no memo between reads, so these tests exercise the serve rules; the
    memo has tests of its own."""
    table = MemoryBoards()
    for name, stand_in in (('store_board', table.store), ('store_approval', table.approve),
                           ('remove_approval', table.unapprove), ('remove_entries', table.remove),
                           ('rows_between', table.between)):
        monkeypatch.setattr(gtp, name, stand_in)
    monkeypatch.setattr(gtp, '_board_cache', {})
    monkeypatch.setattr(gtp, 'BOARD_CACHE_SECONDS', 0)
    monkeypatch.setattr(gtp, '_game_now', lambda: NOON)
    return table


def _log(client, n, lengths=BOARD, category='any', source='typed', ip=None):
    resp = client.post('/guess_the_phrase', headers={'X-Forwarded-For': ip or IPS[n]},
                       json={'action': 'log_board', 'lengths': lengths, 'category': category, 'source': source})
    assert resp.status_code == 200 and resp.get_json() == {'status': 'success'}


def _todays(client):
    resp = client.post('/guess_the_phrase', json={'action': 'todays_board'})
    assert resp.status_code == 200
    return resp.get_json()['board']


def _served(lengths=BOARD, category='any'):
    return {'lengths': lengths, 'category': category}


def _post(client, headers, path, **data):
    resp = client.post('/guess_the_phrase_admin/' + path, headers=headers, data=data)
    assert resp.status_code == 302
    return unquote_plus(resp.headers['Location'])


def _approve(client, headers, lengths='4 2 5 3', category='movie', day=DAY):
    return _post(client, headers, 'approve_board', lengths=lengths, category=category, played_on=day.isoformat(), tab='board')


def _admin(client, monkeypatch, query=''):
    resp = client.get('/guess_the_phrase_admin' + query, headers=_admin_headers(monkeypatch))
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


# --- the game's day ---------------------------------------------------------------------

@pytest.mark.parametrize('now, day', [
    ((2026, 11, 24, 12, 0), date(2026, 11, 23)), ((2026, 11, 24, 23, 29), date(2026, 11, 23)),
    ((2026, 11, 24, 23, 30), date(2026, 11, 24)), ((2026, 11, 25, 0, 5), date(2026, 11, 24)),
    ((2026, 7, 1, 23, 30), date(2026, 7, 1)), ((2027, 1, 1, 10, 0), date(2026, 12, 31)),
])
def test_the_day_is_the_puzzle_that_is_live(monkeypatch, now, day):
    monkeypatch.setattr(gtp, '_game_now', lambda: VILNIUS.localize(datetime(*now)))
    assert gtp._game_today() == day


def _ms(*at):
    return int(VILNIUS.localize(datetime(*at)).timestamp() * 1000)


def test_the_page_hears_the_puzzles_day_and_when_the_next_goes_live(client, boards, monkeypatch):
    reply = client.post('/guess_the_phrase', json={'action': 'todays_board'}).get_json()
    assert reply['day'] == '2026-11-23' and reply['until'] == _ms(2026, 11, 24, 23, 30)
    html = client.get('/guess_the_phrase').get_data(as_text=True)
    assert f"let offerUntil = {reply['until']};" in html and 'let offerDay = "2026-11-23";' in html
    # Over the nights the clocks change, too.
    for now, until in (((2026, 10, 24, 23, 40), (2026, 10, 25, 23, 30)), ((2027, 3, 27, 23, 40), (2027, 3, 28, 23, 30))):
        monkeypatch.setattr(gtp, '_game_now', lambda: VILNIUS.localize(datetime(*now)))
        assert gtp._game_clock() == (date(*now[:3]), _ms(*until))


def test_a_lookup_that_runs_past_the_go_live_gives_the_new_puzzle(client, boards, monkeypatch):
    boards.approve(DAY, '3-9', 'tv')
    boards.approve(DAY + timedelta(days=1), '6-6', 'song')
    clock = [VILNIUS.localize(datetime(2026, 11, 24, 23, 29, 59))]
    monkeypatch.setattr(gtp, '_game_now', lambda: clock[0])
    read = gtp.rows_between

    def slow(first, last):
        clock[0] = VILNIUS.localize(datetime(2026, 11, 24, 23, 30, 2))
        return read(first, last)
    monkeypatch.setattr(gtp, 'rows_between', slow)
    reply = client.post('/guess_the_phrase', json={'action': 'todays_board'}).get_json()
    assert reply == {'board': _served(OTHER, 'song'), 'day': '2026-11-24', 'until': _ms(2026, 11, 25, 23, 30)}


def test_everyone_shares_the_servers_day(client, boards):
    for n in range(3):
        _log(client, n)
    assert {row[5] for row in boards.rows.values()} == {DAY}


# --- what is served ---------------------------------------------------------------------

def test_three_networks_serve_a_board_and_two_do_not(client, boards):
    _log(client, 0)
    _log(client, 1)
    assert _todays(client) is None
    _log(client, 2)
    assert _todays(client) == _served()


def test_one_network_counts_once(client, boards):
    for n, ip in enumerate(ONE_NETWORK):
        _log(client, n, ip=ip)
    _log(client, 5)
    assert _todays(client) is None
    _log(client, 6)
    assert _todays(client) == _served()


def test_the_category_is_the_one_most_players_chose(client, boards):
    for n, category in enumerate(['any', 'movie', 'any', 'movie', 'song']):
        _log(client, n, category=category)
    assert _todays(client) == _served(category='movie')
    _log(client, 6, category='song')
    assert _todays(client) == _served(category='any')      # a tie


def test_a_player_who_changes_category_moves_their_one_vote(client, boards):
    for n in range(3):
        _log(client, n, category='song')
    for n in range(3):
        _log(client, n, category='tv')
    assert len(boards.rows) == 3 and _todays(client) == _served(category='tv')


def test_most_players_win_and_a_tie_goes_to_the_first_there(client, boards):
    for n in range(3):
        _log(client, n, lengths=OTHER)
    for n in range(3, 6):
        _log(client, n)
    assert _todays(client) == _served(OTHER)
    _log(client, 0, lengths=OTHER)                          # a repeat flips nothing
    assert _todays(client) == _served(OTHER)
    _log(client, 7)
    assert _todays(client) == _served()


def test_the_first_words_of_a_longer_board_are_never_served(client, boards):
    for n in range(3):
        _log(client, n, lengths=BOARD[:2])
    assert _todays(client) == _served(BOARD[:2])
    for n in range(3):
        _log(client, n)
    assert _todays(client) == _served()
    # Only whole words count: 4-2 doesn't begin 4-25.
    assert not gtp._starts('4-2', '4-25') and gtp._starts('4-2', '4-2-5')


def test_an_older_puzzle_is_not_offered_again(client, boards):
    for n in range(4):
        boards.store(DAY - timedelta(days=3), f'v{n}', f'p{n}', gtp.shape_text(BOARD), 'any', 'typed')
    for n in range(4):
        _log(client, n)
    assert _todays(client) is None
    for n in range(3):
        _log(client, n, lengths=OTHER)
    assert _todays(client) == _served(OTHER)


def test_one_stray_network_cannot_make_todays_board_look_old(client, boards):
    boards.store(DAY - timedelta(days=1), 'early', 'p', gtp.shape_text(BOARD), 'any', 'typed')
    for n in range(3):
        _log(client, n)
    assert _todays(client) == _served()


def test_a_board_seen_long_ago_is_new_again(client, boards):
    for n in range(4):
        boards.store(DAY - timedelta(days=gtp.PAST_DAYS + 1), f'v{n}', f'p{n}', gtp.shape_text(BOARD), 'any', 'typed')
    for n in range(3):
        _log(client, n)
    assert _todays(client) == _served()


def test_the_admin_and_the_page_look_back_the_same_days(client, boards):
    # The admin tab reads several days at once, so further back than the page.
    for back, board in ((gtp.PAST_DAYS, None), (gtp.PAST_DAYS + 1, _served())):
        boards.rows.clear()
        for n in range(3):
            boards.store(DAY - timedelta(days=back), f'v{n}', f'p{n}', gtp.shape_text(BOARD), 'any', 'typed')
            _log(client, n)
        assert _todays(client) == board
        days, error = gtp._admin_days()
        assert error is None and days[0]['day'] == DAY and days[0]['board'] == board


def test_an_approval_wins_and_only_one_stands(client, boards, monkeypatch):
    headers = _admin_headers(monkeypatch)
    for n in range(5):
        _log(client, n)
    assert 'Approved 6 6 for 11-23' in _approve(client, headers, '6 6', 'song')
    assert _todays(client) == _served(OTHER, 'song')
    assert 'in place of your earlier approval' in _approve(client, headers, '4 2 5 3', 'tv')
    assert _todays(client) == _served(category='tv')
    assert sum(r[2] == gtp.APPROVAL for r in boards.rows.values()) == 1
    assert 'the players' in _post(client, headers, 'unapprove_board', played_on=DAY.isoformat())
    assert _todays(client) == _served()
    assert 'Nothing was approved' in _post(client, headers, 'unapprove_board', played_on=DAY.isoformat())


def test_an_approval_serves_an_older_puzzle_and_is_never_a_player(client, boards, monkeypatch):
    for n in range(4):
        boards.store(DAY - timedelta(days=3), f'v{n}', f'p{n}', gtp.shape_text(BOARD), 'any', 'typed')
    _approve(client, _admin_headers(monkeypatch))
    assert _todays(client) == _served(category='movie')
    _board, groups = gtp.todays_board(list(boards.rows.values()), DAY)
    assert groups[0]['players'] == 0


# --- logging ----------------------------------------------------------------------------

def test_a_loaded_board_cannot_vote_for_itself(client, boards):
    for n in range(3):
        _log(client, n)
    _log(client, 8, source='edited')                        # changed, then changed back
    assert len(boards.rows) == 3
    _log(client, 8, lengths=OTHER, source='edited')
    assert len(boards.rows) == 4


@pytest.mark.parametrize('body', [
    {'lengths': []}, {'lengths': [3]}, {'lengths': 'x'}, {'lengths': [0, 5]}, {'lengths': [21, 2]},
    {'lengths': [4, '2']}, {'lengths': [True, 5]}, {'lengths': [5] * 13}, {'lengths': [20] * 4}, {'lengths': [4.0, 2]},
    {'category': 'nope'}, {'category': 5}, {'source': 'loaded'}, {'source': 'admin'}, {'source': None},
])
def test_malformed_boards_are_a_400_and_store_nothing(client, boards, body):
    sent = {'action': 'log_board', 'lengths': BOARD, 'category': 'any', 'source': 'typed', **body}
    assert client.post('/guess_the_phrase', json=sent).status_code == 400
    assert boards.rows == {}


def test_a_board_never_carries_letters(client, boards):
    _log(client, 0)
    [row] = boards.rows.values()
    assert row[0] == '4-2-5-3' and re.fullmatch(r'[0-9-]+', row[0])


def test_dark_logs_boards_and_offers_none(client, boards, monkeypatch):
    monkeypatch.setattr(gtp, 'GTP_BOARDS_LIVE', False)
    for n in range(4):
        _log(client, n)
    assert len(boards.rows) == 4 and _todays(client) is None
    html = client.get('/guess_the_phrase').get_data(as_text=True)
    assert 'id="gtp-today"' not in html and FAQ_Q not in html and 'const BOARDS_ON = false;' in html


def test_solving_and_adding_a_phrase_still_work(client, boards):
    resp = client.post('/guess_the_phrase', json={'board': '???? ??', 'tried': '', 'category': 'any'})
    assert resp.status_code == 200 and 'advice' in resp.get_json()
    assert client.post('/guess_the_phrase', json={'action': 'nonsense'}).status_code == 400


# --- the memo ---------------------------------------------------------------------------

def test_the_board_is_read_once_a_minute(client, boards, monkeypatch):
    monkeypatch.setattr(gtp, 'BOARD_CACHE_SECONDS', 60)
    _todays(client)
    _todays(client)
    assert boards.reads == 1


def test_a_failed_read_keeps_the_last_board_and_waits(client, boards, monkeypatch):
    alerts = []
    monkeypatch.setattr(gtp.alerts, 'alert_throttled', lambda name, **kw: alerts.append(name))
    for n in range(3):
        _log(client, n)
    assert _todays(client) == _served()

    def down(first, last):
        raise RuntimeError('database down')
    monkeypatch.setattr(gtp, 'rows_between', down)
    assert _todays(client) == _served() and alerts == ['gtp_board_read_failed']
    assert _todays(client) == _served() and len(alerts) == 1


def _overlapping_reads(monkeypatch, boards, first_outcome):
    """Two requests on a cold memo: the first one's read finishes last, as
    first_outcome does. Returns what each was given."""
    monkeypatch.setattr(gtp, 'BOARD_CACHE_SECONDS', 60)
    monkeypatch.setattr(gtp.alerts, 'alert_throttled', lambda *a, **kw: None)
    started, go, given = threading.Event(), threading.Event(), {}
    calls = []

    def read(first, last):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            assert go.wait(10)
            return first_outcome()
        return boards.between(first, last)
    monkeypatch.setattr(gtp, 'rows_between', read)
    thread = threading.Thread(target=lambda: given.update(slow=gtp._board_for(DAY)))
    thread.start()
    assert started.wait(10)
    given['fast'] = gtp._board_for(DAY)
    go.set()
    thread.join(10)
    return given


def test_a_failed_read_never_replaces_another_requests_board(boards, monkeypatch):
    boards.approve(DAY, '4-2-5-3', 'tv')

    def down():
        raise RuntimeError('database down')
    given = _overlapping_reads(monkeypatch, boards, down)
    assert given == {'fast': _served(category='tv'), 'slow': _served(category='tv')}
    assert gtp._board_for(DAY) == _served(category='tv')


def test_an_older_read_that_lands_late_is_not_kept(boards, monkeypatch):
    boards.approve(DAY, '4-2-5-3', 'tv')
    given = _overlapping_reads(monkeypatch, boards, lambda: [])
    assert given == {'fast': _served(category='tv'), 'slow': None}
    assert gtp._board_for(DAY) == _served(category='tv')


def test_a_read_that_works_is_kept_after_one_that_failed(boards, monkeypatch):
    boards.approve(DAY, '4-2-5-3', 'tv')
    monkeypatch.setattr(gtp, 'BOARD_CACHE_SECONDS', 60)
    monkeypatch.setattr(gtp.alerts, 'alert_throttled', lambda *a, **kw: None)
    good, calls = gtp.rows_between, []

    def read(first, last):
        calls.append(1)
        if len(calls) == 1:
            # The second request comes and fails while this one is reading.
            assert gtp._board_for(DAY) is None
            return good(first, last)
        raise RuntimeError('database down')
    monkeypatch.setattr(gtp, 'rows_between', read)
    assert gtp._board_for(DAY) == _served(category='tv')
    assert gtp._board_for(DAY) == _served(category='tv') and len(calls) == 2


def test_an_admin_change_during_a_read_is_not_overwritten(boards, monkeypatch):
    monkeypatch.setattr(gtp, 'BOARD_CACHE_SECONDS', 60)

    def slow(first, last):
        gtp._forget_boards()
        return []
    monkeypatch.setattr(gtp, 'rows_between', slow)
    assert gtp._board_for(DAY) is None and gtp._board_cache == {}


def test_the_page_draws_the_offer_without_reading_the_database(client, boards, monkeypatch):
    html = client.get('/guess_the_phrase').get_data(as_text=True)
    assert boards.reads == 0 and 'const board = null;' in html
    monkeypatch.setattr(gtp, 'BOARD_CACHE_SECONDS', 60)
    for n in range(3):
        _log(client, n, category='movie')
    _todays(client)
    reads = boards.reads
    html = client.get('/guess_the_phrase').get_data(as_text=True)
    assert boards.reads == reads
    first = json.loads(re.search(r'const board = (\{.*?\});', html).group(1))
    assert first == _served(category='movie')


# --- the page ---------------------------------------------------------------------------

def test_the_page_offers_loads_and_takes_back(client, boards):
    html = client.get('/guess_the_phrase').get_data(as_text=True)
    for hook in ('id="gtp-today"', 'id="gtp-load"', 'id="gtp-loaded-note"', 'id="gtp-not-mine"', '↩ Not my board',
                 "action: 'todays_board'", "action: 'log_board'", 'const BOARDS_ON = true;'):
        assert hook in html, hook
    # Between the instructions and the category, above the board it fills.
    assert html.index('id="instructions-panel"') < html.index('id="gtp-today"') < html.index('id="gtp-category"')


def test_the_offer_never_fills_a_letter(client, boards):
    html = client.get('/guess_the_phrase').get_data(as_text=True)
    load = html.split('function loadOffer()')[1].split('function noteShapeChange')[0]
    assert "letters = lengths.flatMap(n => Array(n).fill(''));" in load


def test_the_offer_comes_down_on_time_and_old_replies_are_dropped(client, boards):
    html = client.get('/guess_the_phrase').get_data(as_text=True)
    for hook in ('function watchExpiry()', 'if (seq !== offerSeq) return;', "category + '|' + offerDay",
                 "lengths.join('-') === loadedShape ? 'loaded' : 'edited'"):
        assert hook in html, hook
    assert 'toDateString() ' not in html.split('function noteUse()')[1].split('function ')[0]


def test_faq_names_the_board_and_matches_its_structured_data(client, boards):
    html = client.get('/guess_the_phrase').get_data(as_text=True)
    blocks = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    faq = next(b for b in blocks if b.get('@type') == 'FAQPage')
    answer = next(q for q in faq['mainEntity'] if q['name'] == FAQ_Q)['acceptedAnswer']['text']
    assert answer in html.split('Frequently Asked Questions')[1]


def test_players_are_never_told_how_a_board_is_chosen(client, boards):
    html = client.get('/guess_the_phrase').get_data(as_text=True)
    for told in ('guess_the_phrase_admin', 'approve', 'networks', 'three players', '3 players', 'Vilnius'):
        assert told not in re.sub(r'<!--.*?-->|/\*.*?\*/|^\s*//.*?$', '', html, flags=re.S | re.M), told


# --- the admin tab ----------------------------------------------------------------------

@pytest.fixture
def lists(monkeypatch):
    import crowd
    db = _ScriptedDB()
    monkeypatch.setattr(crowd, 'db_cursor', db)
    return db


def test_the_admin_opens_on_todays_board(client, boards, lists, monkeypatch):
    for n in range(3):
        _log(client, n, category='movie')
    html = _admin(client, monkeypatch)
    assert 'id="board-tab" class="tab-content active"' in html and html.count('class="tab-content active"') == 1
    assert '(live now)' in html and 'serving <span class="shape-text">4 2 5 3</span>' in html
    row = html.split('<span class="shape-text">4 2 5 3</span>')[2].split('</tr>')[0]
    assert '<td>Movie</td>' in row and '<td>3</td>' in row and '>Served<' in row
    assert row.count('<i></i>') == sum(BOARD)


def test_while_dark_the_admin_says_would_serve(client, boards, lists, monkeypatch):
    monkeypatch.setattr(gtp, 'GTP_BOARDS_LIVE', False)
    for n in range(3):
        _log(client, n)
    html = _admin(client, monkeypatch)
    assert '- would serve <span class="shape-text">' in html and '>Would be served<' in html
    assert '- serving <span' not in html and '>Served<' not in html


def test_a_search_opens_on_the_phrases(client, boards, lists, monkeypatch):
    html = _admin(client, monkeypatch, '?q=HELLO')
    assert 'id="reports-tab" class="tab-content active"' in html


@pytest.mark.parametrize('typed', ['4 2 5 3', '4-2-5-3', '4, 2, 5, 3', ' 4  2 5 3 ', '04 2 005 0003', '???? ?? ????? ???', 'TH?S I? _____ ?A?'])
def test_lengths_can_be_typed_several_ways(client, boards, monkeypatch, typed):
    assert 'Approved 4 2 5 3' in _approve(client, _admin_headers(monkeypatch), typed)
    [row] = boards.rows.values()
    assert row[0] == '4-2-5-3'                              # the letters are dropped


@pytest.mark.parametrize('typed', ['', '3', '0 5', '99 2', '1234 5', '0012345 5', 'a1', '<b>', '6 ' * 12, '5 5 5 5 5 5 5 5 5 5 5 5 5'])
def test_a_bad_board_is_refused(client, boards, monkeypatch, typed):
    assert 'error=' in _approve(client, _admin_headers(monkeypatch), typed) and boards.rows == {}


def test_bad_forms_are_refused(client, boards, monkeypatch):
    headers = _admin_headers(monkeypatch)
    assert 'error=' in _post(client, headers, 'approve_board', lengths='4 2 5', category='movie', played_on='soon')
    assert 'error=' in _approve(client, headers, category='nope')
    assert 'error=' in _post(client, headers, 'unapprove_board', played_on='')
    assert 'error=' in _post(client, headers, 'remove_board', lengths='x', played_on=DAY.isoformat())
    assert boards.rows == {}


def test_remove_takes_only_that_days_player_entries(client, boards, monkeypatch):
    headers = _admin_headers(monkeypatch)
    for n in range(3):
        _log(client, n)
    _log(client, 4, lengths=OTHER)
    _approve(client, headers)
    assert 'Removed 3 entries of 4 2 5 3 on 11-23' in _post(
        client, headers, 'remove_board', lengths='4 2 5 3', played_on=DAY.isoformat())
    assert sorted(r[2] for r in boards.rows.values()) == ['admin', 'typed']


def test_board_changes_need_the_password(client):
    for path in ('approve_board', 'unapprove_board', 'remove_board'):
        assert client.post('/guess_the_phrase_admin/' + path, data={}).status_code == 401


def test_each_half_survives_the_others_missing_tables(client, boards, monkeypatch):
    import crowd
    monkeypatch.setattr(crowd, 'db_cursor', _ScriptedDB(fail='gtp_'))
    html = _admin(client, monkeypatch)
    assert 'Has mysql_tables/gtp_crowd.sql been run?' in html and 'id="approve-entry"' in html
    monkeypatch.setattr(crowd, 'db_cursor', _ScriptedDB())

    def down(first, last):
        raise RuntimeError('no table')
    monkeypatch.setattr(gtp, 'rows_between', down)
    html = _admin(client, monkeypatch, '?tab=reports')
    assert 'Has mysql_tables/gtp_boards.sql been run?' in html and 'No open reports' in html


def test_a_failed_write_says_so(client, boards, monkeypatch):
    def down(*a):
        raise RuntimeError('no table')
    for name in ('store_approval', 'remove_approval', 'remove_entries'):
        monkeypatch.setattr(gtp, name, down)
    headers = _admin_headers(monkeypatch)
    assert 'Database error' in _approve(client, headers)
    assert 'Database error' in _post(client, headers, 'unapprove_board', played_on=DAY.isoformat())
    assert 'Database error' in _post(client, headers, 'remove_board', lengths='4 2', played_on=DAY.isoformat())


# --- the SQL ----------------------------------------------------------------------------

def test_logging_is_capped_and_never_raises(client, monkeypatch):
    db = _ScriptedDB(answers={'SELECT COUNT(*) FROM gtp_boards': (gtp.BOARDS_PER_PLAYER_PER_DAY,)})
    monkeypatch.setattr(gtp, 'db_cursor', db)
    gtp.store_board(DAY, 'v', 'p', '4-2', 'any', 'typed')
    assert not db.statements('INSERT INTO gtp_boards')
    db.answers = {'SELECT COUNT(*) FROM gtp_boards': (0,)}
    gtp.store_board(DAY, 'v', 'p', '4-2', 'any', 'typed')
    [(sql, params)] = db.statements('INSERT INTO gtp_boards')
    assert 'ON DUPLICATE KEY UPDATE category = new.category' in sql
    assert params[:6] == (DAY, 'v', 'p', '4-2', 'any', 'typed')
    alerts = []
    monkeypatch.setattr(gtp.alerts, 'alert_throttled', lambda name, **kw: alerts.append(name))
    monkeypatch.setattr(gtp, 'db_cursor', _ScriptedDB(answers=db.answers, fail='INSERT INTO gtp_boards'))
    gtp.store_board(DAY, 'v', 'p', '4-2', 'any', 'typed')
    assert alerts == ['gtp_board_log_failed']


def test_approving_is_one_transaction(monkeypatch):
    db = _ScriptedDB()
    monkeypatch.setattr(gtp, 'db_cursor', db)
    gtp.store_approval(DAY, '4-2', 'tv')
    assert [sql.split(' ')[0] for sql, _p in db.executed] == ['START', 'DELETE', 'INSERT'] and db.commits == 1
    broken = _ScriptedDB(fail='INSERT INTO gtp_boards')
    monkeypatch.setattr(gtp, 'db_cursor', broken)
    with pytest.raises(RuntimeError):
        gtp.store_approval(DAY, '4-2', 'tv')
    assert broken.executed[-1][0] == 'ROLLBACK' and broken.commits == 0


def test_the_table_fits_what_is_stored():
    from functions import phrase_solver as ps
    sql = open('mysql_tables/gtp_boards.sql', encoding='utf-8').read()
    widths = {name: int(n) for name, n in re.findall(r'^\s+(\w+)\s+(?:VAR)?CHAR\((\d+)\)', sql, re.M)}
    assert widths['shape'] >= len(gtp.shape_text([gtp.MAX_WORD_LEN] * gtp.MAX_WORDS))
    assert widths['category'] >= max(len(c) for c in ps.CATEGORY_BY_ID)
    assert widths['source'] >= max(len(s) for s in gtp.BOARD_SOURCES + (gtp.APPROVAL,))
    assert 'UNIQUE KEY uq_board_player_day (shape, voter_hash, played_on)' in sql
    for column in gtp._COLUMNS.split(', '):
        assert re.search(rf'^\s+{column}\s', sql, re.M), column
