"""4x3's today's boards, players' marks, James's answers, tuning and
/4x3_admin (routes/four_by_three.py).

Nothing here reaches a real database: secret_pass points at the production
JawsDB. Most tests swap the module's table functions for an in-memory
stand-in (MemoryBoards); the SQL itself is checked against test_blossom's
scripted fake. Each simulated player is a different X-Forwarded-For, which
is what helpers.client_ip reads. The boards are made up from dictionary
words (datasets/four_by_three_eval.tsv), never a real puzzle's.
"""

import json
import os
from datetime import date, datetime, timedelta

import pytest

from test_blossom import _admin_headers, _ScriptedDB

DAY = date(2026, 11, 20)
# Made-up boards, as a player might type them.
BOARD = ['RACKET', 'BAT', 'OWL', 'WINK', 'SUPER', 'CLUB', 'MOTH', 'BLINK', 'SPIDER']
OTHER = ['BANK', 'SHORE', 'COAST', 'VAULT', 'SAFE', 'TILT', 'LEAN', 'RELY', 'COUNT']
THIRD = ['BARK', 'WOOF', 'GROWL', 'TRUNK', 'BRANCH', 'RIND', 'PEEL', 'SHOUT', 'SNAP']
FOURTH = ['BOLT', 'DASH', 'DART', 'NUT', 'SCREW', 'LATCH', 'LOCK', 'GULP', 'WOLF']
BAT_ANSWER = {'hub': 'BAT', 'pairs': [['CLUB', 'RACKET'], ['OWL', 'MOTH'], ['BLINK', 'WINK'], ['SUPER', 'SPIDER']],
              'names': ['Sports gear', 'Night flyers', '', '___MAN']}


class MemoryBoards:
    """Stands in for four_by_three_boards: the writers write here and the
    readers read back, rows in routes.four_by_three._COLUMNS order."""

    def __init__(self):
        self.rows = {}
        self.clock = datetime(2026, 11, 20, 6, 0)
        self.reads = 0

    def tick(self):
        self.clock += timedelta(minutes=1)
        return self.clock

    def _room(self, voter, day, key):
        import routes.four_by_three as rt
        others = {k for (k, v, d) in self.rows if v == voter and d == day and k != key}
        return len(others) < rt.BOARDS_PER_PLAYER_PER_DAY

    def store(self, day, voter, prefix, key, words, source):
        if not self._room(voter, day, key):
            return
        row = self.rows.get((key, voter, day))
        if row:
            row[1], row[2], row[5], row[6] = words, source, self.tick(), prefix
        else:
            self.rows[(key, voter, day)] = [key, words, source, voter, day, self.tick(), prefix, None, None, None]

    def store_marks(self, day, voter, prefix, key, words, marks):
        if not self._room(voter, day, key):
            return
        row = self.rows.get((key, voter, day))
        if row:
            row[5], row[6], row[7] = self.tick(), prefix, marks
        else:
            self.rows[(key, voter, day)] = [key, words, 'loaded', voter, day, self.tick(), prefix, marks, None, None]

    def approve(self, day, key, words, answers, top, version):
        replaced = [k for k in self.rows if k[1] == 'admin' and k[2] == day and k[0] != key]
        for k in replaced:
            del self.rows[k]
        row = self.rows.get((key, 'admin', day))
        if row:
            row[1], row[5], row[8], row[9] = words, self.tick(), answers, row[9] or top
        else:
            self.rows[(key, 'admin', day)] = [key, words, 'admin', 'admin', day, self.tick(), None, None, answers, top]
        return len(replaced)

    def unapprove(self, key):
        gone = [k for k, row in self.rows.items() if row[0] == key and row[3] == 'admin']
        for k in gone:
            del self.rows[k]
        return len(gone)

    def remove(self, key, day):
        gone = [k for k, row in self.rows.items() if row[0] == key and row[4] == day]
        for k in gone:
            del self.rows[k]
        return len(gone)

    def with_key(self, key):
        self.reads += 1
        return [tuple(row) for row in self.rows.values() if row[0] == key]

    def logged_between(self, first, last):
        self.reads += 1
        keys = {row[0] for row in self.rows.values() if first <= row[4] <= last}
        return [tuple(row) for row in self.rows.values() if row[0] in keys]

    def approved(self, limit):
        rows = [tuple(row) for row in self.rows.values() if row[2] == 'admin' and row[8]]
        return sorted(rows, key=lambda row: row[5], reverse=True)[:limit]

    def mine(self, day=None):
        return [row for row in self.rows.values() if row[2] != 'admin' and (day is None or row[4] == day)]


@pytest.fixture
def boards(monkeypatch):
    """The in-memory table, today's boards live, Pacific's date fixed."""
    import routes.four_by_three as rt
    memory = MemoryBoards()
    monkeypatch.setattr(rt, 'store_board', memory.store)
    monkeypatch.setattr(rt, 'store_marks', memory.store_marks)
    monkeypatch.setattr(rt, 'store_approval', memory.approve)
    monkeypatch.setattr(rt, 'remove_approval', memory.unapprove)
    monkeypatch.setattr(rt, 'remove_board', memory.remove)
    monkeypatch.setattr(rt, 'rows_with_key', memory.with_key)
    monkeypatch.setattr(rt, 'rows_logged_between', memory.logged_between)
    monkeypatch.setattr(rt, 'approved_rows', memory.approved)
    monkeypatch.setattr(rt, 'FOUR_BY_THREE_BOARDS_LIVE', True)
    monkeypatch.setattr(rt, '_pacific_today', lambda: DAY)
    # Every read is fresh: the memo has a test of its own.
    monkeypatch.setattr(rt, 'BOARDS_CACHE_SECONDS', 0)
    monkeypatch.setattr(rt, 'ANSWERS_CACHE_SECONDS', 0)
    return memory


def _player(n):
    return {'X-Forwarded-For': f'198.51.100.{n}' if isinstance(n, int) else n}


def _iso(day):
    return day.isoformat() if isinstance(day, date) else day


def _log(client, player, tiles=BOARD, source='typed', day=DAY):
    body = {'action': 'log_board', 'tiles': tiles, 'source': source, 'played_on': _iso(day)}
    resp = client.post('/4x3', headers=_player(player), json=body)
    assert resp.status_code == 200 and resp.get_json() == {'status': 'success'}


def _mark(client, player, marks, tiles=BOARD, day=DAY):
    body = {'action': 'marks', 'tiles': tiles, 'played_on': _iso(day),
            'marks': [{'tiles': list(t), 'verdict': v} for t, v in marks]}
    resp = client.post('/4x3', headers=_player(player), json=body)
    assert resp.status_code == 200 and resp.get_json() == {'status': 'success'}


def _todays(client, day=DAY, player=99):
    return client.post('/4x3', headers=_player(player),
                       json={'action': 'todays_boards', 'played_on': _iso(day)}).get_json()['boards']


def _served(tiles=BOARD, approved=False):
    return {'tiles': sorted(tiles), 'approved': approved}


def _solve(client, tiles=BOARD, marks=(), player=99):
    body = {'tiles': tiles, 'marks': [{'tiles': list(t), 'verdict': v} for t, v in marks]}
    resp = client.post('/4x3', headers=_player(player), json=body)
    assert resp.status_code == 200
    return resp.get_json()


def _key(tiles=BOARD):
    from functions import four_by_three as fbt
    return fbt.board_key(fbt.parse_board(tiles))


def _label(result, *words):
    return next(g['label'] for g in result['groups'] if set(words) <= set(g['tiles']))


def _approve(client, auth, tiles=BOARD, answer=BAT_ANSWER, day=DAY):
    data = {'tiles': json.dumps(tiles), 'played_on': _iso(day), 'tab': 'boards'}
    if answer:
        data.update(hub=answer['hub'], pairs=json.dumps(answer['pairs']), names=json.dumps(answer['names']))
    return client.post('/4x3_admin/approve_typed_board', headers=auth, data=data)


# --- a day of players ---------------------------------------------------------------

def test_two_players_with_the_same_board_serve_it(client, boards):
    _log(client, 1)
    assert _todays(client) == []
    _log(client, 2)
    assert _todays(client) == [_served()]


def test_a_board_is_its_words_in_any_order_served_alphabetically(client, boards):
    _log(client, 1)
    _log(client, 2, tiles=list(reversed(BOARD)))
    [board] = _todays(client)
    assert board['tiles'] == sorted(BOARD) and board['tiles'] != BOARD


def test_players_on_one_network_count_once(client, boards):
    _log(client, '2001:db8:1:1::5')
    _log(client, '2001:db8:1:2::9')       # the same /48
    assert _todays(client) == []
    _log(client, 7)
    assert _todays(client) == [_served()]


def test_a_loaded_board_is_never_logged(client, boards):
    resp = client.post('/4x3', headers=_player(1),
                       json={'action': 'log_board', 'tiles': BOARD, 'source': 'shared', 'played_on': '2026-11-20'})
    assert resp.status_code == 400
    assert boards.mine() == []


def test_an_edited_board_back_to_the_offered_one_does_not_vote(client, boards):
    _log(client, 1)
    _log(client, 2)
    _log(client, 3, tiles=list(reversed(BOARD)), source='edited')
    assert len(boards.mine()) == 2
    _log(client, 3, tiles=OTHER, source='edited')
    assert len(boards.mine()) == 3


def test_one_player_logs_at_most_ten_boards_a_day(client, boards):
    import routes.four_by_three as rt
    for n in range(rt.BOARDS_PER_PLAYER_PER_DAY + 2):
        _log(client, 1, tiles=BOARD[:8] + ['WORD' + 'ABCDEFGHIJKL'[n]])
    assert len(boards.mine()) == rt.BOARDS_PER_PLAYER_PER_DAY


def test_a_relog_replaces_the_players_row(client, boards):
    _log(client, 1)
    _log(client, 1, tiles=list(reversed(BOARD)))
    [row] = boards.mine()
    assert row[1] == '|'.join(reversed(BOARD))


def test_boards_go_by_the_players_own_date(client, boards):
    tomorrow = DAY + timedelta(days=1)
    # Players already past midnight (Asia, Oceania) log tomorrow's puzzle.
    _log(client, 1, day=tomorrow)
    _log(client, 2, day=tomorrow)
    assert _todays(client, day=tomorrow) == [_served()]
    assert _todays(client, day=DAY) == []
    # A date no clock on Earth shows is a wrong clock: not logged, nothing served.
    _log(client, 3, tiles=OTHER, day=DAY + timedelta(days=2))
    assert len(boards.mine()) == 2
    assert _todays(client, day=DAY - timedelta(days=2)) == []
    resp = client.post('/4x3', json={'action': 'log_board', 'tiles': BOARD, 'source': 'typed', 'played_on': 'soon'})
    assert resp.status_code == 400


def test_the_dark_launch_logs_but_offers_nothing(client, boards, monkeypatch):
    import routes.four_by_three as rt
    monkeypatch.setattr(rt, 'FOUR_BY_THREE_BOARDS_LIVE', False)
    _log(client, 1)
    _log(client, 2)
    assert len(boards.mine()) == 2 and _todays(client) == []
    _approve(client, _admin_headers(monkeypatch))
    # No answers either, on the public page.
    assert _solve(client)['checked'] is False
    auth = _admin_headers(monkeypatch)
    reply = client.post('/4x3_admin/board', headers=auth, json={'action': 'todays_boards', 'played_on': '2026-11-20'})
    assert reply.get_json()['boards'] == [_served(approved=True)]


def test_at_most_three_boards_a_day_approval_first(client, boards, monkeypatch):
    player = 10
    for players, tiles in zip((4, 3, 2), (OTHER, THIRD, FOURTH)):
        for _ in range(players):
            player += 1
            _log(client, player, tiles=tiles)
    assert [b['tiles'] for b in _todays(client)] == [sorted(OTHER), sorted(THIRD), sorted(FOURTH)]
    _approve(client, _admin_headers(monkeypatch))
    assert _todays(client) == [_served(approved=True), _served(OTHER), _served(THIRD)]


def test_a_past_puzzle_is_not_served_again(client, boards, monkeypatch):
    yesterday = DAY - timedelta(days=1)
    _log(client, 1, day=yesterday)
    _log(client, 2, day=yesterday)
    # Two archive players type it today: still yesterday's puzzle.
    _log(client, 3)
    _log(client, 4)
    assert _todays(client) == []
    # Unless James approves it for today.
    _approve(client, _admin_headers(monkeypatch))
    assert _todays(client) == [_served(approved=True)]


def test_one_player_alone_cannot_date_a_board(client, boards):
    _log(client, 1, day=DAY - timedelta(days=1))
    _log(client, 2)
    _log(client, 3)
    assert _todays(client) == [_served()]


def test_todays_boards_are_read_once_a_minute(client, boards, monkeypatch):
    import routes.four_by_three as rt
    monkeypatch.setattr(rt, 'BOARDS_CACHE_SECONDS', 60)
    _todays(client)
    reads = boards.reads
    _todays(client)
    _todays(client)
    assert boards.reads == reads
    # An admin change is seen at once.
    _approve(client, _admin_headers(monkeypatch))
    assert _todays(client) == [_served(approved=True)]


# --- players' marks -------------------------------------------------------------------

RIGHT = (('BAT', 'OWL', 'MOTH'), 'right')


def test_marks_on_a_loaded_board_never_count_toward_serving_it(client, boards):
    _mark(client, 1, [RIGHT])
    _mark(client, 2, [RIGHT])
    [first, second] = boards.mine()
    assert first[2] == 'loaded' and json.loads(first[7]) == [['BAT|OWL|MOTH', 'r']]
    assert _todays(client) == []
    # A logged board keeps its source when its marks come in.
    _log(client, 3)
    _mark(client, 3, [RIGHT])
    assert {row[2] for row in boards.mine()} == {'loaded', 'typed'}


def test_three_networks_confirm_a_group(client, boards):
    for n in (1, 2):
        _mark(client, n, [RIGHT])
    assert _label(_solve(client), 'OWL') != 'players'
    _mark(client, 3, [RIGHT])
    result = _solve(client)
    assert _label(result, 'OWL') == 'players' and result['checked'] is False
    # The player's own mark still reads as theirs.
    assert _label(_solve(client, marks=[RIGHT]), 'OWL') == 'yours'


def test_more_misses_refuse_a_group_and_a_tie_settles_nothing(client, boards):
    refused = (('BAT', 'CLUB', 'RACKET'), 'one_away')
    for n in (1, 2, 3):
        _mark(client, n, [refused])
    assert all(set(g['tiles']) != {'BAT', 'CLUB', 'RACKET'} for g in _solve(client)['groups'])
    for n in (4, 5, 6):
        _mark(client, n, [(('BAT', 'CLUB', 'RACKET'), 'right')])
    tied = _solve(client)
    assert any(set(g['tiles']) == {'BAT', 'CLUB', 'RACKET'} for g in tied['groups'])
    assert _label(tied, 'CLUB') != 'players'


def test_not_sure_which_is_kept_and_counts_as_a_miss(client, boards):
    import routes.four_by_three as rt
    missed = (('BAT', 'CLUB', 'RACKET'), 'missed')
    # The player's own: the solver leaves that group out.
    assert {'BAT', 'CLUB', 'RACKET'} not in [set(g['tiles']) for g in _solve(client, marks=[missed])['groups']]
    for n in (1, 2, 3):
        _mark(client, n, [missed])
    assert json.loads(boards.mine()[0][7]) == [['BAT|CLUB|RACKET', 'm']]
    assert rt.mark_counts(boards.with_key(_key()))[frozenset(('BAT', 'CLUB', 'RACKET'))] == [0, 3]
    # Enough players' misses refuse it for everyone, as One away and Not even close do.
    assert {'BAT', 'CLUB', 'RACKET'} not in [set(g['tiles']) for g in _solve(client)['groups']]


def test_a_players_latest_marks_are_the_ones_that_count(client, boards):
    for n in (1, 2, 3):
        _mark(client, n, [RIGHT])
    _mark(client, 3, [])
    assert _label(_solve(client), 'OWL') != 'players'


def test_groups_players_confirm_must_fit_together(client, boards):
    import routes.four_by_three as rt
    clash = (('OWL', 'MOTH', 'CLUB'), 'right')     # shares two words with RIGHT
    for n in (1, 2, 3, 4):
        _mark(client, n, [RIGHT])
    for n in (5, 6, 7):
        _mark(client, n, [clash])
    confirmed = rt.crowd_answers(boards.with_key(_key()))['players']
    assert confirmed == [frozenset(('BAT', 'OWL', 'MOTH'))]


def test_marks_off_the_board_are_refused(client, boards):
    resp = client.post('/4x3', json={'action': 'marks', 'tiles': BOARD, 'played_on': '2026-11-20',
                                     'marks': [{'tiles': ['BAT', 'OWL', 'NOPE'], 'verdict': 'right'}]})
    assert resp.status_code == 400 and boards.mine() == []


# --- James's answers -------------------------------------------------------------------

def test_admin_pages_need_the_password(client, boards):
    for path in ('/4x3_admin', '/4x3_admin/board'):
        assert client.get(path).status_code == 401
    for path in ('/4x3_admin/approve_typed_board', '/4x3_admin/unapprove_board', '/4x3_admin/remove_board'):
        assert client.post(path).status_code == 401, path


def test_an_approved_answer_is_checked_for_every_player_of_the_board(client, boards, monkeypatch):
    resp = _approve(client, _admin_headers(monkeypatch))
    assert resp.status_code == 302 and 'success=' in resp.headers['Location']
    result = _solve(client, tiles=list(reversed(BOARD)))
    assert result['checked'] is True and result['hub'] == dict(result['hub'], tile='BAT', label='checked')
    assert all(g['label'] == 'checked' for g in result['groups'])
    themes = {frozenset(g['tiles']): g['theme'] for g in result['groups']}
    assert themes[frozenset(('BAT', 'CLUB', 'RACKET'))] == 'Sports gear'
    assert themes[frozenset(('BAT', 'SUPER', 'SPIDER'))] == '___MAN'
    # James's answer beats what players marked.
    for n in (1, 2, 3):
        _mark(client, n, [(('BAT', 'OWL', 'CLUB'), 'right')])
    assert all(g['label'] == 'checked' for g in _solve(client)['groups'])


def test_an_approval_keeps_the_solvers_first_pass_from_before_it(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _approve(client, auth)
    [row] = [r for r in boards.rows.values() if r[2] == 'admin']
    top = json.loads(row[9])
    assert top['hub'] == 'BAT' and len(top['groups']) == 4 and all(len(g) == 3 for g in top['groups'])
    # Saving the answer again keeps the first pass: the solver may be tuned on it by then.
    other_answer = dict(BAT_ANSWER, names=['', '', '', ''])
    _approve(client, auth, answer=other_answer)
    [row] = [r for r in boards.rows.values() if r[2] == 'admin']
    assert json.loads(row[9]) == top and json.loads(row[8])['names'] == ['', '', '', '']


def test_an_approval_without_groups_serves_the_board_alone(client, boards, monkeypatch):
    resp = _approve(client, _admin_headers(monkeypatch), answer=None)
    assert 'success=' in resp.headers['Location']
    assert _todays(client) == [_served(approved=True)]
    [row] = [r for r in boards.rows.values() if r[2] == 'admin']
    assert row[8] is None and row[9] is None
    assert _solve(client)['checked'] is False


def test_one_approval_a_date(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _approve(client, auth)
    resp = _approve(client, auth, tiles=OTHER, answer=None)
    assert 'in+place+of+your+earlier' in resp.headers['Location']
    assert _todays(client) == [_served(OTHER, approved=True)]


@pytest.mark.parametrize('change', [
    {'tiles': 'not json'},
    {'tiles': json.dumps(BOARD[:8])},
    {'played_on': 'someday'},
    {'pairs': json.dumps(BAT_ANSWER['pairs'][:3])},
    {'pairs': json.dumps([['CLUB', 'OWL'], ['RACKET', 'MOTH'], ['BLINK', 'WINK'], ['SUPER', 'BAT']])},
    {'hub': 'NOPE'},
    {'pairs': 'not json'},
])
def test_a_bad_approval_changes_nothing(client, boards, monkeypatch, change):
    data = {'tiles': json.dumps(BOARD), 'played_on': '2026-11-20', 'hub': 'BAT',
            'pairs': json.dumps(BAT_ANSWER['pairs']), 'names': json.dumps(BAT_ANSWER['names'])}
    data.update(change)
    resp = client.post('/4x3_admin/approve_typed_board', headers=_admin_headers(monkeypatch), data=data)
    assert resp.status_code == 302 and 'error=' in resp.headers['Location']
    assert boards.rows == {}


def test_long_names_are_cut_and_odd_ones_dropped(client, boards, monkeypatch):
    answer = dict(BAT_ANSWER, names=['x' * 99, 7, None, '  spaced   out  '])
    _approve(client, _admin_headers(monkeypatch), answer=answer)
    [row] = [r for r in boards.rows.values() if r[2] == 'admin']
    import routes.four_by_three as rt
    assert json.loads(row[8])['names'] == ['x' * rt.NAME_MAX, '', '', 'spaced out']


def test_the_longest_answer_fits_its_column_and_reads_back(client, boards, monkeypatch):
    import re

    import routes.four_by_three as rt
    from functions import four_by_three as fbt
    path = os.path.join(os.path.dirname(__file__), os.pardir, 'mysql_tables', 'four_by_three_boards.sql')
    with open(path, encoding='utf-8') as fh:
        size = int(re.search(r'^\s*answers\s+VARCHAR\((\d+)\)', fh.read(), re.M).group(1))
    # Nine 40-letter words and four names at the most a name can be, of
    # characters JSON writes as 12 each (an emoji).
    tiles = [c * fbt.MAX_TILE_LEN for c in 'ABCDEFGHI']
    names = ['\U0001F600' * rt.NAME_MAX] * fbt.GROUPS
    answer = {'hub': tiles[0], 'pairs': [tiles[1:3], tiles[3:5], tiles[5:7], tiles[7:9]], 'names': names}
    assert 'success=' in _approve(client, _admin_headers(monkeypatch), tiles=tiles, answer=answer).headers['Location']
    [row] = [r for r in boards.rows.values() if r[2] == 'admin']
    assert len(row[8]) <= size
    assert rt.parse_answer(row[8])['names'] == names
    assert {g['theme'] for g in _solve(client, tiles=tiles)['groups']} == {names[0]}


@pytest.mark.parametrize('damaged', [
    {'hub': ['BAT'], 'pairs': BAT_ANSWER['pairs']},
    {'hub': 'BAT', 'pairs': [[['CLUB'], 'RACKET']] + BAT_ANSWER['pairs'][1:]},
    {'hub': 7, 'pairs': BAT_ANSWER['pairs']},
])
def test_a_damaged_answer_is_no_answer_and_players_marks_still_count(client, boards, monkeypatch, damaged):
    import routes.four_by_three as rt
    auth = _admin_headers(monkeypatch)
    _approve(client, auth)
    boards.rows[(_key(), 'admin', DAY)][8] = json.dumps(damaged)
    assert rt.parse_answer(json.dumps(damaged)) is None
    for n in (1, 2, 3):
        _mark(client, n, [RIGHT])
    result = _solve(client)
    assert result['checked'] is False and _label(result, 'OWL') == 'players'
    for tab in ('boards', 'accuracy'):
        assert client.get(f'/4x3_admin?tab={tab}', headers=auth).status_code == 200


def test_undo_and_remove(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1)
    _approve(client, auth)
    resp = client.post('/4x3_admin/unapprove_board', headers=auth, data={'key': _key()})
    assert 'success=' in resp.headers['Location'] and _todays(client) == []
    resp = client.post('/4x3_admin/unapprove_board', headers=auth, data={'key': _key()})
    assert 'error=' in resp.headers['Location']
    _log(client, 2)
    assert _todays(client) == [_served()]
    resp = client.post('/4x3_admin/remove_board', headers=auth, data={'key': _key(), 'played_on': DAY.isoformat()})
    assert 'success=' in resp.headers['Location'] and boards.rows == {} and _todays(client) == []
    for bad in ({'key': 'x', 'played_on': '2026-11-20'}, {'key': _key(), 'played_on': 'never'}):
        assert 'error=' in client.post('/4x3_admin/remove_board', headers=auth, data=bad).headers['Location']


def test_the_admin_page_shows_each_date_and_why(client, boards, monkeypatch):
    _log(client, 1)
    _log(client, 2)
    _log(client, 3, tiles=OTHER)
    _log(client, 4, tiles=THIRD, day=DAY + timedelta(days=1))
    _mark(client, 5, [RIGHT])
    html = client.get('/4x3_admin', headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert '<h3>2026-11-21</h3>' in html and '<h3>2026-11-20</h3>' in html
    assert 'Served' in html and 'needs 1 more player' in html
    assert f'/4x3_admin/board?key={_key()}&amp;day=2026-11-20' in html
    assert '<span>BAT</span>' in html and 'Hub BAT' in html
    assert 'BAT + MOTH + OWL: <span class="right">1</span>' in html
    assert 'defaults (0 of 10 answers to start tuning)' in html


def test_the_accuracy_tab_scores_the_first_pass_before_each_answer(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _approve(client, auth)
    html = client.get('/4x3_admin?tab=accuracy', headers=auth).get_data(as_text=True)
    assert 'Hub:</strong> 1 / 1 right' in html and 'Groups:</strong> 4 / 4 right' in html
    assert 'BAT + CLUB + RACKET' in html and '(Sports gear)' in html
    assert '<td>hub_cos</td>' in html


def test_only_a_boards_first_answer_keeps_a_first_pass(client, boards, monkeypatch):
    import routes.four_by_three as rt
    auth = _admin_headers(monkeypatch)
    old = DAY - timedelta(days=rt.ACCURACY_DAYS + 10)
    _approve(client, auth, day=old)
    # A first pass that stands out, to see which one the tab scores.
    standout = [['BAT', 'CLUB', 'guess'], ['RACKET', 'MOTH', 'guess'], ['BLINK', 'WINK', 'guess'],
                ['SUPER', 'SPIDER', 'guess']]
    boards.rows[(_key(), 'admin', old)][9] = json.dumps({'hub': 'OWL', 'hub_label': 'guess', 'groups': standout})
    # Answered again for a later date: the solver may be tuned on it by
    # then, so that approval keeps no first pass of its own.
    _approve(client, auth, day=DAY)
    assert boards.rows[(_key(), 'admin', DAY)][9] is None
    # Listed by its latest date, and scored on its first answer's first pass,
    # however old that is.
    html = client.get('/4x3_admin?tab=accuracy', headers=auth).get_data(as_text=True)
    assert 'Hub OWL <span class="wrong">wrong</span>' in html
    # Without that first answer, there is nothing fair to score.
    client.post('/4x3_admin/remove_board', headers=auth, data={'key': _key(), 'played_on': old.isoformat()})
    html = client.get('/4x3_admin?tab=accuracy', headers=auth).get_data(as_text=True)
    assert 'not kept' in html and 'Hub:</strong>' not in html


def test_the_board_entry_page_opens_a_board_and_never_logs_or_marks(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1, tiles=list(reversed(BOARD)))
    html = client.get(f'/4x3_admin/board?key={_key()}&day=2026-11-20', headers=auth).get_data(as_text=True)
    preload = json.loads(html.split('const PRELOAD = ', 1)[1].split(';\n', 1)[0])
    assert preload['tiles'] == list(reversed(BOARD)) and preload['hub'] == 'BAT' and len(preload['pairs']) == 4
    assert "the solver's groups" in preload['note']
    assert 'value="2026-11-20"' in html and 'Approve' in html
    assert 'Frequently Asked Questions' not in html and 'More daily puzzle solvers' not in html
    for body in ({'action': 'log_board', 'tiles': BOARD, 'source': 'typed', 'played_on': '2026-11-20'},
                 {'action': 'marks', 'tiles': BOARD, 'marks': [], 'played_on': '2026-11-20'}):
        assert client.post('/4x3_admin/board', headers=auth, json=body).status_code == 400
    assert len(boards.mine()) == 1
    assert client.post('/4x3_admin/board', headers=auth, json={'tiles': BOARD}).status_code == 200
    # Once answered, it opens with James's own answer.
    _approve(client, auth)
    html = client.get(f'/4x3_admin/board?key={_key()}', headers=auth).get_data(as_text=True)
    preload = json.loads(html.split('const PRELOAD = ', 1)[1].split(';\n', 1)[0])
    assert preload['names'] == BAT_ANSWER['names'] and 'your answer' in preload['note']
    assert 'Not a board' in client.get('/4x3_admin/board?key=nope', headers=auth).get_data(as_text=True)
    missing = client.get(f"/4x3_admin/board?key={'0' * 40}", headers=auth).get_data(as_text=True)
    assert 'No board has that key' in missing


def test_the_editor_opens_the_answer_players_get_and_when_it_was_saved(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _approve(client, auth)                                              # saved 11-20 06:01
    later = dict(BAT_ANSWER, names=['Gear', 'Flyers', 'Eyes', 'Heroes'])
    _approve(client, auth, answer=later, day=DAY + timedelta(days=1))   # saved 11-20 06:02
    html = client.get(f'/4x3_admin/board?key={_key()}&day={DAY.isoformat()}', headers=auth).get_data(as_text=True)
    preload = json.loads(html.split('const PRELOAD = ', 1)[1].split(';\n', 1)[0])
    assert preload['names'] == later['names'] and preload['note'] == 'your answer from 11-20 06:02'


# --- tuning ---------------------------------------------------------------------------

def _eval_boards():
    path = os.path.join(os.path.dirname(__file__), os.pardir, 'datasets', 'four_by_three_eval.tsv')
    out = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            if line.strip() and not line.startswith('#'):
                hub, *pairs = line.rstrip('\n').split('\t')
                out.append((hub.upper(), [p.upper().split(',') for p in pairs]))
    return out


def _answer_boards(boards, n):
    """James approves n made-up boards with their answers, a date apart."""
    from functions import four_by_three as fbt
    for k, (hub, pairs) in enumerate(_eval_boards()[:n]):
        tiles = [hub] + [w for p in pairs for w in p]
        boards.approve(DAY - timedelta(days=k), fbt.board_key(tiles), '|'.join(tiles),
                       json.dumps({'hub': hub, 'pairs': pairs, 'names': [''] * 4}), None, None)


@pytest.fixture
def stale_tuning(monkeypatch):
    import routes.four_by_three as rt
    monkeypatch.setattr(rt, '_tuning', {'value': None, 'expires': 0.0})


def test_ten_answers_tune_the_weights_and_each_approval_refits(client, boards, stale_tuning, monkeypatch):
    import routes.four_by_three as rt
    from functions import four_by_three as fbt
    _answer_boards(boards, rt.MIN_TUNING_BOARDS - 1)
    assert rt.current_weights() == (fbt.DEFAULT_WEIGHTS, rt.MIN_TUNING_BOARDS - 1)
    _answer_boards(boards, rt.MIN_TUNING_BOARDS)
    rt._forget_tuning()
    weights, n = rt.current_weights()
    assert n == rt.MIN_TUNING_BOARDS and weights != fbt.DEFAULT_WEIGHTS and min(weights) >= 0
    # Read once, then kept, until an approval clears it: the next made-up
    # board, on a date with no approval yet (one approval a date).
    assert rt.current_weights() == (weights, n)
    hub, pairs = _eval_boards()[rt.MIN_TUNING_BOARDS]
    _approve(client, _admin_headers(monkeypatch), tiles=[hub] + [w for p in pairs for w in p],
             answer={'hub': hub, 'pairs': pairs, 'names': [''] * 4}, day=DAY + timedelta(days=1))
    assert rt._tuning['expires'] == 0.0
    assert rt.current_weights()[1] == rt.MIN_TUNING_BOARDS + 1
    html = client.get('/4x3_admin?tab=accuracy', headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert f'tuned on your {rt.MIN_TUNING_BOARDS + 1} answers' in html


def test_tuning_off_keeps_the_defaults(boards, stale_tuning, monkeypatch):
    import routes.four_by_three as rt
    from functions import four_by_three as fbt
    _answer_boards(boards, 12)
    monkeypatch.setattr(rt, 'AUTO_TUNE', False)
    assert rt.current_weights() == (fbt.DEFAULT_WEIGHTS, 0)


def test_a_failed_tuning_read_keeps_the_defaults_and_alerts(stale_tuning, capsys):
    import routes.four_by_three as rt
    from functions import four_by_three as fbt
    from monitoring import alerts
    alerts.reset_throttle()
    assert rt.current_weights() == (fbt.DEFAULT_WEIGHTS, 0)
    assert 'check=four_by_three_tuning_failed' in capsys.readouterr().out


def test_an_answer_that_does_not_fit_its_board_is_no_example(boards, stale_tuning):
    import routes.four_by_three as rt
    from functions import four_by_three as fbt
    _answer_boards(boards, rt.MIN_TUNING_BOARDS + 1)
    approvals = iter([r for r in boards.rows.values() if r[2] == 'admin'])
    next(approvals)[8] = json.dumps({'hub': 'NOPE', 'pairs': [], 'names': []})
    # Tuned on the others, and the count is of the answers it was fit on.
    weights, n = rt.current_weights()
    assert n == rt.MIN_TUNING_BOARDS and weights != fbt.DEFAULT_WEIGHTS
    next(approvals)[8] = json.dumps({'hub': 'NOPE', 'pairs': [], 'names': []})
    rt._forget_tuning()
    assert rt.current_weights() == (fbt.DEFAULT_WEIGHTS, rt.MIN_TUNING_BOARDS - 1)


# --- the SQL itself ---------------------------------------------------------------------

def test_the_writes_say_what_they_mean(monkeypatch):
    import routes.four_by_three as rt
    db = _ScriptedDB(answers={'SELECT COUNT(*)': (0,)})
    monkeypatch.setattr(rt, 'db_cursor', db)
    rt.store_board(DAY, 'v' * 32, 'p' * 32, _key(), '|'.join(BOARD), 'typed')
    [(sql, params)] = db.statements('INSERT INTO four_by_three_boards')
    assert 'AS new ON DUPLICATE KEY UPDATE words = new.words, source = new.source' in sql
    assert params[0] == DAY and params[4] == '|'.join(BOARD) and sql.count('%s') == len(params)
    rt.store_marks(DAY, 'v' * 32, 'p' * 32, _key(), '|'.join(BOARD), '[]')
    sql, params = db.statements('INSERT INTO four_by_three_boards')[-1]
    assert 'ON DUPLICATE KEY UPDATE marks = new.marks' in sql and 'source = new' not in sql
    assert params[5] == 'loaded' and sql.count('%s') == len(params)
    rt.store_approval(DAY, _key(), '|'.join(BOARD), '{}', '{"hub": "BAT"}', 'abcd1234')
    sql, params = db.statements('INSERT INTO four_by_three_boards')[-1]
    assert params[1] == 'admin' and params[4] == 'admin' and sql.count('%s') == len(params)
    assert 'COALESCE(four_by_three_boards.solver_top, new.solver_top)' in sql
    steps = [s.split(' ')[0] for s, _ in db.executed[-3:]]
    assert steps == ['START', 'DELETE', 'INSERT']
    assert db.executed[-2][1] == ('admin', DAY, _key())
    failing = _ScriptedDB(fail='INSERT INTO four_by_three_boards')
    monkeypatch.setattr(rt, 'db_cursor', failing)
    with pytest.raises(RuntimeError):
        rt.store_approval(DAY, _key(), '|'.join(BOARD), None, None, None)
    assert failing.executed[-1][0] == 'ROLLBACK' and failing.commits == 0
    monkeypatch.setattr(rt, 'db_cursor', db)
    assert rt.remove_approval(_key()) == 1
    assert db.statements('DELETE FROM four_by_three_boards WHERE board_key = %s AND voter_hash = %s')
    assert rt.remove_board(_key(), DAY) == 1
    rt.rows_logged_between(DAY, DAY)
    assert db.statements('WHERE board_key IN (SELECT board_key FROM four_by_three_boards WHERE played_on BETWEEN')
    rt.approved_rows(5)
    [(sql, params)] = db.statements('WHERE source = %s AND answers IS NOT NULL')
    assert params == ('admin', 5) and 'ORDER BY updated_at DESC LIMIT %s' in sql


def test_a_player_over_the_daily_limit_logs_nothing(monkeypatch):
    import routes.four_by_three as rt
    db = _ScriptedDB(answers={'SELECT COUNT(*)': (rt.BOARDS_PER_PLAYER_PER_DAY,)})
    monkeypatch.setattr(rt, 'db_cursor', db)
    rt.store_board(DAY, 'v' * 32, None, _key(), '|'.join(BOARD), 'typed')
    rt.store_marks(DAY, 'v' * 32, None, _key(), '|'.join(BOARD), '[]')
    assert not db.statements('INSERT INTO four_by_three_boards')


def test_a_failed_log_or_read_alerts_and_the_page_carries_on(client, capsys):
    from monitoring import alerts
    alerts.reset_throttle()
    _log(client, 1)
    _mark(client, 1, [RIGHT])
    assert _todays(client) == []
    assert _solve(client)['hub']['tile'] == 'BAT'
    out = capsys.readouterr().out
    assert 'check=four_by_three_board_log_failed' in out and 'check=four_by_three_board_read_failed' in out
