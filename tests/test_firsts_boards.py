"""Firsts' page, today's boards, players' marks and /firsts_admin
(routes/firsts.py).

Nothing here reaches a real database: secret_pass points at the production
JawsDB. Most tests swap the module's table functions for an in-memory
stand-in (MemoryBoards); the SQL itself is checked against test_blossom's
scripted fake. Each simulated player is a different X-Forwarded-For. The
boards and clues are made up, never a real puzzle's.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from test_blossom import _admin_headers, _ScriptedDB

DAY = date(2026, 11, 27)        # No. 002
TODAY_N = 2
# A made-up board: CAT runs down the first column from a green C.
TILES = list('CATSDOGEMUPIGRATBEE')
GREEN = [True] + [False] * 18
CLUES = [[f'Made-up clue {i + 1}', '3'] for i in range(10)]
CLUES[0][0] = 'A pet that purrs'


def _body(tiles=TILES, green=GREEN, clues=CLUES, **extra):
    return dict({'tiles': list(tiles), 'green': list(green), 'clues': [list(c) for c in clues]}, **extra)


def _key(tiles=TILES, green=GREEN, clues=CLUES):
    return ''.join(tiles) + ':' + ''.join('1' if g else '0' for g in green) + ':' + ';'.join(c[1] for c in clues)


KEY = _key()
OTHER_TILES = list('COTSDOGEMUPIGRATBEE')
OTHER = _key(OTHER_TILES)


class MemoryBoards:
    """Stands in for firsts_boards: the writers write here and the readers
    read back, rows in routes.firsts._COLUMNS order."""

    def __init__(self):
        self.rows = {}
        self.clock = datetime(2026, 11, 27, 6, 0)

    def tick(self):
        self.clock += timedelta(minutes=1)
        return self.clock

    def _row(self, key, voter, day, **changes):
        old = self.rows.get((key, voter, day))
        row = list(old) if old else [key, None, None, voter, day, None, None, None, None, None]
        for name, value in changes.items():
            row[['key', 'details', 'source', 'voter', 'day', 'updated', 'prefix', 'marks', 'answers', 'top']
                .index(name)] = value
        row[5] = self.tick()
        self.rows[(key, voter, day)] = tuple(row)

    def _under_cap(self, voter, day, key):
        import routes.firsts as rt
        return len({k for (k, v, d) in self.rows if v == voter and d == day and k != key}) < rt.BOARDS_PER_PLAYER_PER_DAY

    def store(self, day, voter, prefix, key, details, source):
        if self._under_cap(voter, day, key):
            self._row(key, voter, day, details=details, source=source, prefix=prefix)

    def store_marks(self, day, voter, prefix, key, marks):
        if not self._under_cap(voter, day, key):
            return
        old = self.rows.get((key, voter, day))
        self._row(key, voter, day, marks=marks, prefix=prefix, source=old[2] if old else 'loaded')

    def approve(self, day, key, details, answers, top, version):
        replaced = [k for k in self.rows if k[1] == 'admin' and k[2] == day and k[0] != key]
        for k in replaced:
            del self.rows[k]
        old = self.rows.get((key, 'admin', day))
        self._row(key, 'admin', day, details=details, source='admin', answers=answers,
                  top=old[9] if old and old[9] else top)
        return len(replaced)

    def _drop(self, keep):
        gone = [k for k in self.rows if not keep(k)]
        for k in gone:
            del self.rows[k]
        return len(gone)

    def unapprove(self, key):
        return self._drop(lambda k: not (k[0] == key and k[1] == 'admin'))

    def remove(self, key, day):
        return self._drop(lambda k: not (k[0] == key and k[2] == day))

    def logged_between(self, first, last):
        keys = {k[0] for k in self.rows if first <= k[2] <= last}
        return [row for k, row in self.rows.items() if k[0] in keys]

    def with_key(self, key):
        return [row for k, row in self.rows.items() if k[0] == key]

    def approved_days(self, before, limit):
        rows = sorted(((k[2], row[1]) for k, row in self.rows.items() if k[1] == 'admin' and k[2] < before),
                      reverse=True)
        return rows[:limit]

    def mine(self):
        return [row for row in self.rows.values() if row[2] != 'admin']


@pytest.fixture
def boards(monkeypatch):
    """The in-memory table, boards live, the game's date fixed."""
    import routes.firsts as rt
    memory = MemoryBoards()
    monkeypatch.setattr(rt, 'store_board', memory.store)
    monkeypatch.setattr(rt, 'store_marks', memory.store_marks)
    monkeypatch.setattr(rt, 'store_approval', memory.approve)
    monkeypatch.setattr(rt, 'remove_approval', memory.unapprove)
    monkeypatch.setattr(rt, 'remove_board', memory.remove)
    monkeypatch.setattr(rt, 'rows_logged_between', memory.logged_between)
    monkeypatch.setattr(rt, 'rows_with_key', memory.with_key)
    monkeypatch.setattr(rt, 'approved_days', memory.approved_days)
    monkeypatch.setattr(rt, 'FIRSTS_BOARDS_LIVE', True)
    monkeypatch.setattr(rt, '_game_today', lambda: DAY)
    # Every read is fresh: the memos are the same pattern as /4x6's.
    monkeypatch.setattr(rt, 'BOARDS_CACHE_SECONDS', 0)
    monkeypatch.setattr(rt, 'ANSWERS_CACHE_SECONDS', 0)
    return memory


def _player(n):
    return {'X-Forwarded-For': f'198.51.100.{n}' if isinstance(n, int) else n}


def _log(client, player, tiles=TILES, source='typed', n=None, **extra):
    body = dict(_body(tiles, **extra), action='log_board', source=source)
    if n is not None:
        body['n'] = n
    resp = client.post('/firsts', headers=_player(player), json=body)
    assert resp.status_code == 200 and resp.get_json() == {'status': 'success'}


def _todays(client, n=None, player=99):
    body = {'action': 'todays_boards'}
    if n is not None:
        body['n'] = n
    return client.post('/firsts', headers=_player(player), json=body).get_json()


def _keys(reply):
    return [b['key'] for b in reply['boards']]


def _mark(client, player, marks, key=KEY):
    resp = client.post('/firsts', headers=_player(player), json={'action': 'marks', 'key': key, 'marks': marks})
    assert resp.status_code == 200


def _approve(client, auth, tiles=TILES, n=TODAY_N, answers=None, clues=CLUES):
    data = {'n': str(n), 'board': json.dumps(_body(tiles, clues=clues))}
    if answers is not None:
        data['answers'] = json.dumps(answers)
    return client.post('/firsts_admin/approve_typed_board', headers=auth, data=data)


def _solve(client, **extra):
    resp = client.post('/firsts', json=_body(**extra))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


# --- solves -------------------------------------------------------------------------

def test_a_solve_lists_each_clues_traceable_words(client):
    reply = _solve(client)
    assert len(reply['clues']) == 10 and reply['complete'] is True
    first = reply['clues'][0]
    assert first['label'] == 'guess' and first['items'] and first['items'][0]['path'][0] == 0
    words = [it['word'] for it in first['items']]
    assert 'CAT' in words


def test_a_clue_with_no_length_is_skipped(client):
    clues = [['A pet', '3']] + [['', '']] * 9
    reply = _solve(client, clues=clues)
    assert reply['clues'][0]['items'] and all(c['items'] == [] for c in reply['clues'][1:])


@pytest.mark.parametrize('change', [
    {'tiles': TILES[:-1]}, {'tiles': TILES[:-1] + ['AB']}, {'tiles': TILES[:-1] + ['5']},
    {'green': [1] * 19}, {'green': GREEN[:-1]}, {'gone': 'no'}, {'clues': 'x'},
    {'clues': [['a', '3']] * 11}, {'clues': [['a', '99']]}, {'clues': [['a']]}, {'clues': [['a', 3]]},
])
def test_bad_requests_are_400s(client, change):
    assert client.post('/firsts', json=dict(_body(), **change)).status_code == 400


def test_the_page_renders_with_its_marker_and_beta_tag(client):
    html = client.get('/firsts').get_data(as_text=True)
    assert 'id="fst-ok"' in html and 'jj-beta-tag' in html
    assert 'Firsts Answers &amp; Hints' in html and 'Not affiliated with or endorsed by Bleppo Games' in html


def test_the_faq_matches_its_json_ld(client):
    html = client.get('/firsts').get_data(as_text=True)
    ld = next(json.loads(block) for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
              if '"FAQPage"' in block)
    for q in ld['mainEntity']:
        assert q['name'] in html and q['acceptedAnswer']['text'][:60] in html, q['name']


def test_the_page_never_says_how_many_players_serve_a_board(client):
    import routes.firsts as rt
    html = client.get('/firsts').get_data(as_text=True)
    assert not re.search(r'\b(two|three|%d|%d) (players|networks)' % (rt.SERVE_NETWORKS, rt.CONFIRM_NETWORKS),
                         html, re.I)


# --- today's boards ---------------------------------------------------------------

def test_two_players_with_the_same_board_serve_it(client, boards):
    _log(client, 1)
    assert _todays(client)['boards'] == []
    _log(client, 2)
    reply = _todays(client)
    assert _keys(reply) == [KEY] and reply['n'] == TODAY_N
    assert reply['boards'][0]['details']['clues'][0] == ['A pet that purrs', '3']


def test_players_on_one_network_count_once(client, boards):
    _log(client, '2001:db8:1:1::5')
    _log(client, '2001:db8:1:2::9')       # the same /48
    assert _todays(client)['boards'] == []
    _log(client, 7)
    assert _keys(_todays(client)) == [KEY]


@pytest.mark.parametrize('change', [
    {'gone': [False] * 18 + [True]},                 # a tile gone: partway through
    {'green': [False] * 19},                         # no green
    {'clues': [['a', '3']] * 9 + [['b', '']]},       # a length missing
])
def test_only_a_whole_starting_board_is_logged(client, boards, change):
    body = dict(_body(), action='log_board', source='typed', **change)
    assert client.post('/firsts', headers=_player(1), json=body).status_code == 200
    assert boards.rows == {}


def test_an_edited_board_back_to_the_offered_one_does_not_vote(client, boards):
    _log(client, 1)
    _log(client, 2)
    _log(client, 3, source='edited')
    assert len(boards.mine()) == 2


def test_the_clue_text_most_players_typed_is_served(client, boards):
    clues = [list(c) for c in CLUES]
    clues[1][0] = 'made-up CLUE 2!'
    _log(client, 1, clues=clues)
    _log(client, 2)
    _log(client, 3)
    served = _todays(client)['boards'][0]['details']['clues']
    assert served[1][0] == 'Made-up clue 2'


def test_a_future_puzzle_is_never_logged_or_offered(client, boards, monkeypatch):
    _log(client, 1, n=TODAY_N + 1)
    _log(client, 2, n=TODAY_N + 1)
    assert boards.rows == {}
    _approve(client, _admin_headers(monkeypatch), n=TODAY_N + 1)
    assert _todays(client, n=TODAY_N + 1)['boards'] == []
    # On its day it serves like any other.
    monkeypatch.setattr('routes.firsts._game_today', lambda: DAY + timedelta(days=1))
    assert _keys(_todays(client)) == [KEY]


def test_an_older_puzzle_is_offered_once_approved(client, boards, monkeypatch):
    _log(client, 1, n=TODAY_N - 1)
    _log(client, 2, n=TODAY_N - 1)
    assert _todays(client, n=TODAY_N - 1)['boards'] == []
    _approve(client, _admin_headers(monkeypatch), n=TODAY_N - 1)
    reply = _todays(client, n=TODAY_N - 1)
    assert _keys(reply) == [KEY] and reply['boards'][0]['approved'] is True
    assert reply['past'] == [{'n': 1, 'label': '001', 'title': ''}]


@pytest.mark.parametrize('n', ['2', 2.0, True, -3, TODAY_N + 61])
def test_a_bad_puzzle_number_is_a_400(client, boards, n):
    assert client.post('/firsts', json={'action': 'todays_boards', 'n': n}).status_code == 400


def test_the_dark_launch_logs_but_offers_nothing(client, boards, monkeypatch):
    monkeypatch.setattr('routes.firsts.FIRSTS_BOARDS_LIVE', False)
    _log(client, 1)
    _log(client, 2)
    assert len(boards.mine()) == 2 and _todays(client)['boards'] == []


# --- marks and answers ---------------------------------------------------------------

def test_three_networks_saying_a_word_worked_serve_it_as_players_found(client, boards):
    for p in (1, 2):
        _mark(client, p, {'0': {'CAT': 'right'}})
    assert _solve(client, key=KEY)['clues'][0]['label'] == 'guess'
    _mark(client, 3, {'0': {'CAT': 'right'}})
    first = _solve(client, key=KEY)['clues'][0]
    assert first['label'] == 'players' and first['known'] == 'CAT'
    # Without the starting board's key nothing is served.
    assert _solve(client)['clues'][0]['label'] == 'guess'


def test_a_word_three_networks_say_was_refused_leaves_the_list(client, boards):
    for p in (1, 2, 3):
        _mark(client, p, {'0': {'CAT': 'miss'}})
    words = [it['word'] for it in _solve(client, key=KEY)['clues'][0]['items']]
    assert 'CAT' not in words


def test_a_players_word_that_does_not_fit_the_length_is_never_served(client, boards):
    for p in (1, 2, 3):
        _mark(client, p, {'0': {'CATS': 'right'}})
    assert _solve(client, key=KEY)['clues'][0]['label'] == 'guess'


def test_the_players_own_words_and_solved_clues(client):
    reply = _solve(client, yours={'0': 'CAT'}, solved=[0])
    first = reply['clues'][0]
    assert first['label'] == 'yours' and first['solved'] is True and first['items'] == []


@pytest.mark.parametrize('marks', [{'x': {'CAT': 'right'}}, {'0': {'CAT': 'yes'}}, {'0': {'C': 'right'}},
                                   {'0': {f'CAT{"S" * i}': 'right' for i in range(7)}}, ['CAT']])
def test_bad_marks_are_400s(client, boards, marks):
    resp = client.post('/firsts', json={'action': 'marks', 'key': KEY, 'marks': marks})
    assert resp.status_code == 400


def test_marks_need_a_board_key(client, boards):
    resp = client.post('/firsts', json={'action': 'marks', 'key': 'nope', 'marks': {'0': {'CAT': 'right'}}})
    assert resp.status_code == 400


# --- admin ------------------------------------------------------------------------

def test_admin_pages_need_the_password(client, boards):
    for path in ('/firsts_admin', '/firsts_admin/board'):
        assert client.get(path).status_code == 401
    for path in ('/firsts_admin/board', '/firsts_admin/approve_typed_board', '/firsts_admin/unapprove_board',
                 '/firsts_admin/remove_board'):
        assert client.post(path).status_code == 401, path


def test_an_approval_with_answers_serves_them_as_checked(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    resp = _approve(client, auth, answers={'words': {'0': 'CAT'}, 'readings': {}})
    assert resp.status_code == 302 and 'success=' in resp.headers['Location']
    assert _keys(_todays(client)) == [KEY]
    first = _solve(client, key=KEY)['clues'][0]
    assert first['label'] == 'checked' and first['known'] == 'CAT'
    # The solver's own first pick was kept with the approval, before any answer.
    row = boards.rows[(KEY, 'admin', DAY)]
    assert json.loads(row[9])[0] and json.loads(row[8]) == {'words': {'0': 'CAT'}, 'readings': {}}
    # Saving again keeps that first pick.
    _approve(client, auth, answers={'words': {'0': 'CAT', '1': 'DOG'}, 'readings': {}})
    assert boards.rows[(KEY, 'admin', DAY)][9] == row[9]


def test_one_approval_a_date(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _approve(client, auth)
    resp = _approve(client, auth, tiles=OTHER_TILES)
    assert 'in+place+of' in resp.headers['Location']
    assert _keys(_todays(client)) == [OTHER]


@pytest.mark.parametrize('change', [{'n': ''}, {'n': 'x'}, {'n': str(TODAY_N + 61)}, {'board': ''},
                                    {'board': '[]'}, {'board': json.dumps(_body(green=[False] * 19))},
                                    {'answers': '{bad'}])
def test_a_bad_approval_changes_nothing(client, boards, monkeypatch, change):
    data = dict({'n': str(TODAY_N), 'board': json.dumps(_body())}, **change)
    resp = client.post('/firsts_admin/approve_typed_board', headers=_admin_headers(monkeypatch), data=data)
    assert 'error=' in resp.headers['Location'] and boards.rows == {}


def test_undo_and_remove(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1)
    _approve(client, auth)
    client.post('/firsts_admin/unapprove_board', headers=auth, data={'key': KEY})
    assert _todays(client)['boards'] == [] and len(boards.mine()) == 1
    resp = client.post('/firsts_admin/unapprove_board', headers=auth, data={'key': KEY})
    assert 'error=' in resp.headers['Location']
    _approve(client, auth)
    resp = client.post('/firsts_admin/remove_board', headers=auth, data={'key': KEY, 'played_on': DAY.isoformat()})
    assert 'Removed+2+entries' in resp.headers['Location'] and boards.rows == {}
    for data in ({'key': KEY, 'played_on': 'x'}, {'played_on': DAY.isoformat()}, {'key': KEY, 'played_on': '2026-02-30'}):
        resp = client.post('/firsts_admin/remove_board', headers=auth, data=data)
        assert 'error=' in resp.headers['Location'], data


def test_the_admin_page_shows_answers_and_flags_a_changed_board(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _approve(client, auth, answers={'words': {'0': 'CAT'}, 'readings': {}})
    _mark(client, 1, {'1': {'DOG': 'right'}})
    html = client.get('/firsts_admin', headers=auth).get_data(as_text=True)
    assert 'No. 002' in html and '(today)' in html and 'CAT' in html and 'DOG 1' in html
    assert 'players agree on a different board' not in html
    _log(client, 1, tiles=OTHER_TILES)
    _log(client, 2, tiles=OTHER_TILES)
    html = client.get('/firsts_admin', headers=auth).get_data(as_text=True)
    assert 'players agree on a different board' in html


def test_the_board_entry_page_opens_a_board_and_never_logs(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _approve(client, auth, answers={'words': {'0': 'CAT'}, 'readings': {}})
    html = client.get('/firsts_admin/board?key=' + KEY + '&n=2', headers=auth).get_data(as_text=True)
    assert 'Approve your game' in html and '"CAT"' in html and KEY in html
    bad = client.get('/firsts_admin/board?key=nope', headers=auth).get_data(as_text=True)
    assert 'Not a board' in bad
    before = dict(boards.rows)
    for action in ('log_board', 'marks'):
        resp = client.post('/firsts_admin/board', headers=auth, json=dict(_body(), action=action, source='typed'))
        assert resp.status_code == 400
    assert client.post('/firsts_admin/board', headers=auth, json=_body(key=KEY)).status_code == 200
    assert boards.rows == before


def test_the_board_entry_page_takes_numbers_weeks_ahead(client, boards, monkeypatch):
    """James enters future puzzles: the No. box takes up to AHEAD_DAYS on,
    ?n= opens that number; players' No. box stops at today's."""
    import routes.firsts as rt
    auth = _admin_headers(monkeypatch)
    html = client.get(f'/firsts_admin/board?n={TODAY_N + 2}', headers=auth).get_data(as_text=True)
    assert f'let n = {TODAY_N + 2};' in html and f'const LAST_N = {TODAY_N + rt.AHEAD_DAYS};' in html
    html = client.get(f'/firsts_admin/board?n={TODAY_N + rt.AHEAD_DAYS + 1}', headers=auth).get_data(as_text=True)
    assert f'let n = {TODAY_N};' in html and 'id="fst-paste-text"' in html
    html = client.get('/firsts').get_data(as_text=True)
    assert 'let n = TODAY_N;' in html and f'const LAST_N = {TODAY_N};' in html
    assert 'id="fst-paste-text"' not in html and "'fst-paste-text'" not in html, "players don't get the paste box"


ADMIN_POSTS =('/firsts_admin/board', '/firsts_admin/approve_typed_board', '/firsts_admin/unapprove_board',
               '/firsts_admin/remove_board')


@pytest.mark.parametrize('path', ADMIN_POSTS)
@pytest.mark.parametrize('sent', [{'Sec-Fetch-Site': 'cross-site'}, {'Origin': 'https://evil.example'}])
def test_another_sites_post_is_refused_password_and_all(client, boards, monkeypatch, path, sent):
    _approve(client, _admin_headers(monkeypatch))
    before = dict(boards.rows)
    resp = client.post(path, headers=dict(_admin_headers(monkeypatch), **sent),
                       data={'key': KEY, 'n': '2', 'played_on': DAY.isoformat(), 'board': json.dumps(_body())})
    assert resp.status_code == 403 and boards.rows == before


def test_a_database_error_is_said_on_the_admin_page(client, monkeypatch):
    monkeypatch.setattr('routes.firsts._game_today', lambda: DAY)
    html = client.get('/firsts_admin', headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert 'firsts_boards.sql' in html


def test_hostile_text_is_escaped(client, boards, monkeypatch):
    clues = [list(c) for c in CLUES]
    clues[0][0] = '<script>alert(1)</script>'
    _approve(client, _admin_headers(monkeypatch), clues=clues)
    html = client.get('/firsts_admin', headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert '<script>alert(1)' not in html
    page = client.get('/firsts_admin/board?key=' + KEY, headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert '<script>alert(1)' not in page


# --- the SQL itself ---------------------------------------------------------------------

FREE = {'GET_LOCK': (1,), 'RELEASE_LOCK': (1,), 'SELECT COUNT(*)': (0,)}


def test_the_writes_say_what_they_mean(monkeypatch):
    import routes.firsts as rt
    db = _ScriptedDB(answers=dict(FREE))
    monkeypatch.setattr(rt, 'db_cursor', db)
    rt.store_board(DAY, 'v' * 32, 'p' * 32, KEY, '{}', 'typed')
    assert [s.split('(')[0] for s, _ in db.executed] == [
        'SELECT GET_LOCK', 'SELECT COUNT', 'INSERT INTO firsts_boards ', 'SELECT RELEASE_LOCK']
    lock = 'firsts:' + 'v' * 32 + ':2026-11-27'
    assert db.executed[0][1] == (lock, rt.LOCK_SECONDS) and len(lock) <= 64
    [(sql, params)] = db.statements('INSERT INTO firsts_boards')
    assert 'AS new ON DUPLICATE KEY UPDATE details = new.details' in sql
    assert params[:6] == (DAY, 'v' * 32, 'p' * 32, KEY, '{}', 'typed') and sql.count('%s') == len(params)
    rt.store_marks(DAY, 'v' * 32, 'p' * 32, KEY, '{"0":{"CAT":"right"}}')
    sql, params = db.statements('INSERT INTO firsts_boards')[-1]
    assert 'ON DUPLICATE KEY UPDATE marks = new.marks' in sql and params[4] == 'loaded'
    assert sql.count('%s') == len(params)
    rt.store_approval(DAY, KEY, '{}', '{"words":{}}', '["CAT"]', 1)
    sql, params = db.statements('INSERT INTO firsts_boards')[-1]
    assert 'COALESCE(firsts_boards.solver_top, new.solver_top)' in sql
    assert params[:5] == (DAY, 'admin', KEY, '{}', 'admin') and sql.count('%s') == len(params)
    assert [s.split(' ')[0] for s, _ in db.executed[-5:]] == ['SELECT', 'START', 'DELETE', 'INSERT', 'SELECT']
    failing = _ScriptedDB(answers=dict(FREE), fail='INSERT INTO firsts_boards')
    monkeypatch.setattr(rt, 'db_cursor', failing)
    with pytest.raises(RuntimeError):
        rt.store_approval(DAY, KEY, '{}', None, None, 1)
    assert [s.split('(')[0] for s, _ in failing.executed[-2:]] == ['ROLLBACK', 'SELECT RELEASE_LOCK']
    monkeypatch.setattr(rt, 'db_cursor', db)
    assert rt.remove_approval(KEY) == 1 and db.executed[-1][1] == (KEY, 'admin')
    assert rt.remove_board(KEY, DAY) == 1 and db.executed[-1][1] == (KEY, DAY)
    rt.rows_logged_between(DAY, DAY)
    assert db.statements('WHERE board_key IN (SELECT board_key FROM firsts_boards WHERE played_on BETWEEN')


def test_a_player_over_the_daily_limit_logs_nothing(monkeypatch):
    import routes.firsts as rt
    db = _ScriptedDB(answers=dict(FREE, **{'SELECT COUNT(*)': (rt.BOARDS_PER_PLAYER_PER_DAY,)}))
    monkeypatch.setattr(rt, 'db_cursor', db)
    rt.store_board(DAY, 'v' * 32, None, KEY, '{}', 'typed')
    rt.store_marks(DAY, 'v' * 32, None, KEY, '{}')
    assert not db.statements('INSERT INTO firsts_boards')


def test_the_table_file_matches_the_columns():
    import os
    import routes.firsts as rt
    sql = open(os.path.join(os.path.dirname(__file__), os.pardir, 'mysql_tables', 'firsts_boards.sql'),
               encoding='utf-8').read()
    for column in [c.strip() for c in rt._COLUMNS.split(',')]:
        assert re.search(r'^\s+%s\s' % column, sql, re.M), column
    assert 'UNIQUE KEY uq_board_player_day (board_key, voter_hash, played_on)' in sql


def test_the_game_day_and_puzzle_numbers():
    import routes.firsts as rt
    assert rt.number_of(date(2026, 11, 26)) == 1 and rt.day_of(30) == date(2026, 12, 25)
    assert rt.label_of(2) == '002' and rt.label_of(-2) == '-02' and rt.label_of(0) == '000'


@pytest.mark.parametrize('utc, n', [((2026, 11, 28, 13, 29), 2), ((2026, 11, 28, 13, 31), 3),
                                    ((2026, 11, 29, 5, 0), 3), ((2026, 11, 29, 13, 30), 4)])
def test_the_game_day_turns_over_at_the_sites_time(monkeypatch, utc, n):
    import routes.firsts as rt

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(*utc, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(rt, 'datetime', Clock)
    assert rt.number_of(rt._game_today()) == n
