"""4x6's page, today's boards and /4x6_admin (routes/four_by_six.py).

Nothing here reaches a real database: secret_pass points at the production
JawsDB. Most tests swap the module's table functions for an in-memory
stand-in (MemoryBoards); the SQL itself is checked against test_blossom's
scripted fake. Each simulated player is a different X-Forwarded-For, which
is what helpers.client_ip reads. The boards are made up (seeded shuffles),
never a real puzzle's.
"""

import json
import random
import re
from datetime import date, datetime, timedelta

import pytest

from functions import four_by_six as fbs
from test_blossom import _admin_headers, _ScriptedDB

DAY = date(2026, 11, 23)


def _shuffles(want_solution):
    rng = random.Random(46)
    while True:
        tiles = list(fbs.COLORS * 4)
        rng.shuffle(tiles)
        text = ''.join(tiles)
        if (fbs.solve(fbs.parse_board(text)) is not None) == want_solution:
            yield text


_good = _shuffles(True)
BOARD, OTHER, THIRD, FOURTH = (next(_good) for _ in range(4))
MORE = [next(_good) for _ in range(12)]
STUCK = next(_shuffles(False))


class MemoryBoards:
    """Stands in for four_by_six_boards: the writers write here and the
    readers read back, rows in routes.four_by_six._COLUMNS order."""

    def __init__(self):
        self.rows = {}
        self.clock = datetime(2026, 11, 23, 6, 0)
        self.reads = 0

    def tick(self):
        self.clock += timedelta(minutes=1)
        return self.clock

    def store(self, day, voter, prefix, board, source):
        import routes.four_by_six as rt
        others = {b for (b, v, d) in self.rows if v == voter and d == day and b != board}
        if len(others) >= rt.BOARDS_PER_PLAYER_PER_DAY:
            return
        self.rows[(board, voter, day)] = (board, source, voter, day, self.tick(), prefix)

    def approve(self, day, board):
        replaced = [k for k in self.rows if k[1] == 'admin' and k[2] == day and k[0] != board]
        for k in replaced:
            del self.rows[k]
        self.rows[(board, 'admin', day)] = (board, 'admin', 'admin', day, self.tick(), None)
        return len(replaced)

    def _drop(self, keep):
        gone = [k for k in self.rows if not keep(k)]
        for k in gone:
            del self.rows[k]
        return len(gone)

    def unapprove(self, board):
        return self._drop(lambda k: not (k[0] == board and k[1] == 'admin'))

    def remove(self, board, day):
        return self._drop(lambda k: not (k[0] == board and k[2] == day))

    def logged_between(self, first, last):
        self.reads += 1
        boards = {k[0] for k in self.rows if first <= k[2] <= last}
        return [row for k, row in self.rows.items() if k[0] in boards]

    def mine(self):
        return [row for row in self.rows.values() if row[1] != 'admin']


@pytest.fixture
def boards(monkeypatch):
    """The in-memory table, today's boards live, Pacific's date fixed."""
    import routes.four_by_six as rt
    memory = MemoryBoards()
    monkeypatch.setattr(rt, 'store_board', memory.store)
    monkeypatch.setattr(rt, 'store_approval', memory.approve)
    monkeypatch.setattr(rt, 'remove_approval', memory.unapprove)
    monkeypatch.setattr(rt, 'remove_board', memory.remove)
    monkeypatch.setattr(rt, 'rows_logged_between', memory.logged_between)
    monkeypatch.setattr(rt, 'FOUR_BY_SIX_BOARDS_LIVE', True)
    monkeypatch.setattr(rt, '_pacific_today', lambda: DAY)
    # Every read is fresh: the memo has a test of its own.
    monkeypatch.setattr(rt, 'BOARDS_CACHE_SECONDS', 0)
    return memory


def _player(n):
    return {'X-Forwarded-For': f'198.51.100.{n}' if isinstance(n, int) else n}


def _log(client, player, board=BOARD, source='typed', day=DAY):
    body = {'action': 'log_board', 'board': board, 'source': source, 'played_on': day.isoformat()}
    resp = client.post('/4x6', headers=_player(player), json=body)
    assert resp.status_code == 200 and resp.get_json() == {'status': 'success'}


def _todays(client, day=DAY, player=99):
    return client.post('/4x6', headers=_player(player),
                       json={'action': 'todays_boards', 'played_on': day.isoformat()}).get_json()['boards']


def _served(board=BOARD, approved=False):
    return {'board': board, 'approved': approved}


def _approve(client, auth, board=BOARD, day=DAY):
    return client.post('/4x6_admin/approve_typed_board', headers=auth,
                       data={'board': board, 'played_on': day.isoformat()})


# --- solves -------------------------------------------------------------------------

def test_a_solve_is_the_engines_plan(client):
    reply = client.post('/4x6', json={'board': BOARD}).get_json()
    assert reply == json.loads(json.dumps(fbs.plan(fbs.parse_board(BOARD))))
    assert reply['status'] == 'start' and reply['par'] == reply['fewest'] + 2


def test_a_solve_from_the_players_moves(client):
    steps = fbs.solve(fbs.parse_board(BOARD))
    moves = [[s.src, s.dst] for s in steps[:4]]
    reply = client.post('/4x6', json={'board': BOARD, 'moves': moves}).get_json()
    assert reply['status'] == 'on_track' and reply['made'] == 4 and reply['left'] == len(steps) - 4
    assert reply['position'] == list(steps[3].after)


# A board from partway through a game: the first move of BOARD's solution made.
def _partway():
    position = fbs.parse_board(BOARD)
    first = fbs.solve(position)[0]
    return fbs.board_text(fbs.apply_move(position, first.src, first.dst))


def test_a_board_from_partway_through_a_game_is_solved(client):
    reply = client.post('/4x6', json={'board': _partway()}).get_json()
    assert reply['start_board'] is False and reply['fewest'] == len(fbs.solve(fbs.parse_board(BOARD))) - 1


def test_the_rainbow_route_only_when_asked_for(client):
    assert 'rainbow' not in client.post('/4x6', json={'board': BOARD}).get_json()
    route = client.post('/4x6', json={'board': BOARD, 'rainbow': True}).get_json()['rainbow']
    assert route['moves'] == len(route['steps'])
    assert 'rainbow' not in client.post('/4x6', json={'board': BOARD, 'rainbow': 'yes'}).get_json()


def test_a_board_from_partway_through_a_game_is_never_logged_or_approved(client, boards, monkeypatch):
    for n in range(1, 5):
        _log(client, n, board=_partway())
    assert boards.rows == {} and _todays(client) == []
    auth = _admin_headers(monkeypatch)
    resp = _approve(client, auth, board=_partway())
    assert 'error=' in resp.headers['Location'] and boards.rows == {}
    html = client.get('/4x6_admin/board', headers=auth, query_string={'board': _partway()}).get_data(as_text=True)
    assert 'const PRELOAD = null' in html and 'Not a board' in html


def test_a_board_with_no_solution_says_so(client):
    reply = client.post('/4x6', json={'board': STUCK}).get_json()
    assert reply['solvable'] is False and reply['status'] == 'unsolvable' and 'steps' not in reply


@pytest.mark.parametrize('body', [
    {}, {'board': ''}, {'board': None}, {'board': BOARD[:-1]}, {'board': BOARD[:-1] + 'X'},
    {'board': 'Y' * 24}, {'board': BOARD, 'moves': 'x'}, {'board': BOARD, 'moves': [[0]]},
    {'board': BOARD, 'moves': [[0, 9]]}, {'board': BOARD, 'moves': [[6, 0]]},
    {'board': BOARD, 'moves': [[True, False]]}, {'board': BOARD, 'moves': [['0', '1']]},
    {'board': BOARD, 'moves': [[0, 6]] * 61}, {'action': 'nope'},
    {'action': 'log_board', 'board': BOARD, 'source': 'shared', 'played_on': '2026-11-23'},
    {'action': 'log_board', 'board': BOARD, 'source': 'typed', 'played_on': 'soon'},
    {'action': 'todays_boards', 'played_on': 'soon'},
])
def test_bad_requests_are_400s(client, boards, body):
    assert client.post('/4x6', json=body).status_code == 400
    assert boards.rows == {}


# --- the page -----------------------------------------------------------------------

def test_the_page_renders_with_its_marker_and_beta_tag(client):
    html = client.get('/4x6').get_data(as_text=True)
    assert 'id="fbs-ok"' in html and 'jj-beta-tag' in html
    assert '4x6 Answers &amp; Hints' in html and 'Play 4x6' in html
    assert 'fbs-approve-form' not in html and '4x6_admin' not in html


def test_the_faq_matches_its_json_ld(client):
    html = client.get('/4x6').get_data(as_text=True)
    blobs = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    faq = next(b for b in blobs if b.get('@type') == 'FAQPage')
    names = [q['name'] for q in faq['mainEntity']]
    assert len(names) == 10 and len(set(names)) == 10
    import html as html_lib
    page = html_lib.unescape(html)
    for q in faq['mainEntity']:
        assert page.count(q['acceptedAnswer']['text']) >= 1, q['name']


def test_the_page_never_rushes_players_or_says_how_boards_are_served(client):
    html = client.get('/4x6').get_data(as_text=True).lower()
    for word in ('under 90 seconds', 'under 45 seconds', 'blitz', 'lightning', 'network', 'two players'):
        assert word not in html, word
    assert chr(0x2014) not in html   # no em dashes


def test_the_points_on_the_page_are_the_engines(client):
    html = client.get('/4x6').get_data(as_text=True)
    scoring = json.loads(re.search(r'const SCORING = (\{.*?\});', html).group(1))
    for day in range(7):
        top = (scoring['solved'] + scoring['per_day'] * day + scoring['no_tools'] + scoring['no_undos']
               + scoring['under_par'] + scoring['fewest'])
        assert top == fbs.points(day, 13, 13)


# --- a day of players ---------------------------------------------------------------

def test_two_players_with_the_same_board_serve_it(client, boards):
    _log(client, 1)
    assert _todays(client) == []
    _log(client, 2)
    assert _todays(client) == [_served()]


def test_players_on_one_network_count_once(client, boards):
    _log(client, '2001:db8:1:1::5')
    _log(client, '2001:db8:1:2::9')       # the same /48
    assert _todays(client) == []
    _log(client, 7)
    assert _todays(client) == [_served()]


def test_a_board_with_no_solution_is_never_logged(client, boards):
    for n in range(1, 5):
        _log(client, n, board=STUCK)
    assert boards.rows == {} and _todays(client) == []


def test_a_stored_board_with_no_solution_or_damaged_is_never_served(client, boards, monkeypatch):
    for n, bad in enumerate((STUCK, 'JUNK')):
        for voter in ('a', 'b', 'c'):
            boards.rows[(bad, voter, DAY)] = (bad, 'typed', voter, DAY, boards.tick(), voter)
    _log(client, 1)
    _log(client, 2)
    assert _todays(client) == [_served()]
    html = client.get('/4x6_admin', headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert html.count('has no solution') == 2


def test_an_edited_board_back_to_the_offered_one_does_not_vote(client, boards):
    _log(client, 1)
    _log(client, 2)
    _log(client, 3, source='edited')
    assert len(boards.mine()) == 2
    _log(client, 3, board=OTHER, source='edited')
    assert len(boards.mine()) == 3


def test_one_player_logs_at_most_ten_boards_a_day(client, boards):
    import routes.four_by_six as rt
    for board in MORE:
        _log(client, 1, board=board)
    assert len(boards.mine()) == rt.BOARDS_PER_PLAYER_PER_DAY


def test_boards_go_by_the_players_own_date(client, boards):
    tomorrow = DAY + timedelta(days=1)
    # Players already past midnight (Asia, Oceania) log tomorrow's board.
    _log(client, 1, day=tomorrow)
    _log(client, 2, day=tomorrow)
    assert _todays(client, day=tomorrow) == [_served()]
    assert _todays(client, day=DAY) == []
    # A date no clock on Earth shows is a wrong clock: not logged, nothing served.
    _log(client, 3, board=OTHER, day=DAY + timedelta(days=2))
    assert len(boards.mine()) == 2
    assert _todays(client, day=DAY - timedelta(days=2)) == []


def test_the_dark_launch_logs_but_offers_nothing(client, boards, monkeypatch):
    import routes.four_by_six as rt
    monkeypatch.setattr(rt, 'FOUR_BY_SIX_BOARDS_LIVE', False)
    _log(client, 1)
    _log(client, 2)
    assert len(boards.mine()) == 2 and _todays(client) == []
    reply = client.post('/4x6_admin/board', headers=_admin_headers(monkeypatch),
                        json={'action': 'todays_boards', 'played_on': DAY.isoformat()})
    assert reply.get_json()['boards'] == [_served()]
    assert 'id="fbs-shared"' not in client.get('/4x6').get_data(as_text=True)


def test_at_most_three_boards_a_day_approval_first(client, boards, monkeypatch):
    player = 10
    for players, board in zip((4, 3, 2), (OTHER, THIRD, FOURTH)):
        for _ in range(players):
            player += 1
            _log(client, player, board=board)
    assert [b['board'] for b in _todays(client)] == [OTHER, THIRD, FOURTH]
    _approve(client, _admin_headers(monkeypatch))
    assert _todays(client) == [_served(approved=True), _served(OTHER), _served(THIRD)]


def test_a_past_board_is_not_served_again(client, boards, monkeypatch):
    yesterday = DAY - timedelta(days=1)
    _log(client, 1, day=yesterday)
    _log(client, 2, day=yesterday)
    # Two tabs left open past midnight: still yesterday's board.
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
    import routes.four_by_six as rt
    monkeypatch.setattr(rt, 'BOARDS_CACHE_SECONDS', 60)
    _todays(client)
    reads = boards.reads
    _todays(client)
    _todays(client)
    assert boards.reads == reads
    # An admin change is seen at once.
    _approve(client, _admin_headers(monkeypatch))
    assert _todays(client) == [_served(approved=True)]


# --- admin ----------------------------------------------------------------------------

def test_admin_pages_need_the_password(client, boards):
    for path in ('/4x6_admin', '/4x6_admin/board'):
        assert client.get(path).status_code == 401
    for path in ('/4x6_admin/board', '/4x6_admin/approve_typed_board', '/4x6_admin/unapprove_board',
                 '/4x6_admin/remove_board'):
        assert client.post(path).status_code == 401, path


def test_approving_serves_at_once_and_one_a_date(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    resp = _approve(client, auth)
    assert resp.status_code == 302 and 'success=' in resp.headers['Location']
    assert _todays(client) == [_served(approved=True)]
    resp = _approve(client, auth, board=OTHER)
    assert 'in+place+of' in resp.headers['Location']
    assert _todays(client) == [_served(OTHER, approved=True)]
    _approve(client, auth, board=THIRD, day=DAY + timedelta(days=1))
    assert _todays(client) == [_served(OTHER, approved=True)]


@pytest.mark.parametrize('change', [{'board': ''}, {'board': BOARD[:-1]}, {'board': STUCK}, {'played_on': ''},
                                    {'played_on': '2026-13-01'}])
def test_a_bad_approval_changes_nothing(client, boards, monkeypatch, change):
    data = dict({'board': BOARD, 'played_on': DAY.isoformat()}, **change)
    resp = client.post('/4x6_admin/approve_typed_board', headers=_admin_headers(monkeypatch), data=data)
    assert 'error=' in resp.headers['Location'] and boards.rows == {}


def test_undo_and_remove(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1)
    _approve(client, auth)
    client.post('/4x6_admin/unapprove_board', headers=auth, data={'board': BOARD})
    assert _todays(client) == [] and len(boards.mine()) == 1
    resp = client.post('/4x6_admin/unapprove_board', headers=auth, data={'board': BOARD})
    assert 'error=' in resp.headers['Location']
    _approve(client, auth)
    resp = client.post('/4x6_admin/remove_board', headers=auth, data={'board': BOARD, 'played_on': DAY.isoformat()})
    assert 'Removed+2+entries' in resp.headers['Location'] and boards.rows == {}
    for data in ({'board': BOARD, 'played_on': 'x'}, {'board': '', 'played_on': DAY.isoformat()},
                 {'board': 'Y' * 25, 'played_on': DAY.isoformat()}, {'board': BOARD, 'played_on': '2026-02-30'}):
        resp = client.post('/4x6_admin/remove_board', headers=auth, data=data)
        assert 'error=' in resp.headers['Location'], data


def test_the_admin_page_shows_each_date_its_par_and_why(client, boards, monkeypatch):
    _log(client, 1)
    _log(client, 2, board=OTHER, day=DAY + timedelta(days=1))
    _log(client, 3, board=OTHER, day=DAY + timedelta(days=1))
    html = client.get('/4x6_admin', headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    fewest = len(fbs.solve(fbs.parse_board(BOARD)))
    assert f'Par {fewest + 2}' in html and 'needs 1 more player' in html and 'Served' in html
    assert '2026-11-24' in html and '(today)' in html
    assert f'/4x6_admin/board?board={BOARD}&amp;day=2026-11-23' in html


def test_the_board_entry_page_opens_a_board_and_never_logs(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    html = client.get(f'/4x6_admin/board?board={BOARD}&day=2026-11-22', headers=auth).get_data(as_text=True)
    assert f'const PRELOAD = "{BOARD}"' in html and 'value="2026-11-22"' in html
    assert 'fourBySixApproveState_v1' in html
    html = client.get('/4x6_admin/board?board=JUNK', headers=auth).get_data(as_text=True)
    assert 'Not a board' in html and 'const PRELOAD = null' in html
    assert client.post('/4x6_admin/board', headers=auth, json={'board': BOARD}).get_json()['solvable']
    resp = client.post('/4x6_admin/board', headers=auth, json={'action': 'log_board', 'board': BOARD, 'source': 'typed',
                                                               'played_on': DAY.isoformat()})
    assert resp.status_code == 400 and boards.rows == {}


def test_a_database_error_is_said_on_the_admin_page(client, monkeypatch):
    auth = _admin_headers(monkeypatch)      # no table: conftest's cursor fails
    html = client.get('/4x6_admin', headers=auth).get_data(as_text=True)
    assert 'four_by_six_boards.sql' in html
    for path, data in (('approve_typed_board', {'board': BOARD, 'played_on': DAY.isoformat()}),
                       ('unapprove_board', {'board': BOARD}),
                       ('remove_board', {'board': BOARD, 'played_on': DAY.isoformat()})):
        resp = client.post('/4x6_admin/' + path, headers=auth, data=data)
        assert 'Database+error' in resp.headers['Location'], path


# --- the SQL itself ---------------------------------------------------------------------

def test_the_writes_say_what_they_mean(monkeypatch):
    import routes.four_by_six as rt
    db = _ScriptedDB(answers={'SELECT COUNT(*)': (0,)})
    monkeypatch.setattr(rt, 'db_cursor', db)
    rt.store_board(DAY, 'v' * 32, 'p' * 32, BOARD, 'typed')
    [(sql, params)] = db.statements('INSERT INTO four_by_six_boards')
    assert 'AS new ON DUPLICATE KEY UPDATE source = new.source' in sql
    assert params[:5] == (DAY, 'v' * 32, 'p' * 32, BOARD, 'typed') and sql.count('%s') == len(params)
    rt.store_approval(DAY, BOARD)
    sql, params = db.statements('INSERT INTO four_by_six_boards')[-1]
    assert params[:4] == (DAY, 'admin', BOARD, 'admin') and sql.count('%s') == len(params)
    assert [s.split(' ')[0] for s, _ in db.executed[-3:]] == ['START', 'DELETE', 'INSERT']
    assert db.executed[-2][1] == ('admin', DAY, BOARD)
    failing = _ScriptedDB(fail='INSERT INTO four_by_six_boards')
    monkeypatch.setattr(rt, 'db_cursor', failing)
    with pytest.raises(RuntimeError):
        rt.store_approval(DAY, BOARD)
    assert failing.executed[-1][0] == 'ROLLBACK' and failing.commits == 0
    monkeypatch.setattr(rt, 'db_cursor', db)
    assert rt.remove_approval(BOARD) == 1
    assert db.executed[-1][1] == (BOARD, 'admin')
    assert rt.remove_board(BOARD, DAY) == 1
    assert db.executed[-1][1] == (BOARD, DAY)
    rt.rows_logged_between(DAY, DAY)
    assert db.statements('WHERE board IN (SELECT board FROM four_by_six_boards WHERE played_on BETWEEN')


def test_a_player_over_the_daily_limit_logs_nothing(monkeypatch):
    import routes.four_by_six as rt
    db = _ScriptedDB(answers={'SELECT COUNT(*)': (rt.BOARDS_PER_PLAYER_PER_DAY,)})
    monkeypatch.setattr(rt, 'db_cursor', db)
    rt.store_board(DAY, 'v' * 32, None, BOARD, 'typed')
    assert not db.statements('INSERT INTO four_by_six_boards')


def test_a_failed_log_or_read_alerts_and_the_page_carries_on(client, capsys, monkeypatch):
    import routes.four_by_six as rt
    from monitoring import alerts
    monkeypatch.setattr(rt, '_pacific_today', lambda: DAY)
    alerts.reset_throttle()
    _log(client, 1)
    assert _todays(client) == []
    assert client.post('/4x6', json={'board': BOARD}).get_json()['solvable']
    out = capsys.readouterr()
    text = out.out + out.err
    assert 'four_by_six_board_log_failed' in text and 'four_by_six_board_read_failed' in text


# --- the offer in the first paint ------------------------------------------------------

def _first_paint(html):
    return json.loads(re.search(r'const byDay = (.*?);\n', html).group(1))


def test_the_page_arrives_with_the_boards_its_last_lookup_found(client, boards, monkeypatch):
    import routes.four_by_six as rt
    monkeypatch.setattr(rt, 'BOARDS_CACHE_SECONDS', 60)
    _log(client, 1)
    _log(client, 2)
    assert _first_paint(client.get('/4x6').get_data(as_text=True)) is None
    fetched = _todays(client)
    reads = boards.reads
    assert _first_paint(client.get('/4x6').get_data(as_text=True)) == {'2026-11-23': fetched}
    assert boards.reads == reads
    _approve(client, _admin_headers(monkeypatch), board=OTHER)
    assert _first_paint(client.get('/4x6').get_data(as_text=True)) is None


# --- hostile input -----------------------------------------------------------------------

HOSTILE = ["' OR '1'='1", "'; DROP TABLE four_by_six_boards; --", '1 UNION SELECT 1,2,3', '%s', '%(x)s', "\'",
           '\x00', 'admin', '<script>alert(1)</script>', '"><img src=x onerror=1>', '{{7*7}}', '\u212a' * 24,
           '\u017f' * 24, '\U0001f600' * 24, BOARD + "'", BOARD[:-1] + "'", 'K' * 24, 'A' * 5000]
NOT_TEXT = [None, True, 0, -1, 1.5, 10 ** 30, [], {}, [BOARD], {'$gt': ''}]
_STORED_BOARD = re.compile('[' + fbs.COLORS + ']{24}')


@pytest.mark.parametrize('field', ['board', 'moves', 'action', 'source', 'played_on', 'first_solve'])
def test_hostile_values_never_reach_the_sql_or_crash(client, monkeypatch, field):
    import routes.four_by_six as rt
    db = _ScriptedDB(answers={'SELECT COUNT(*)': (0,)})
    monkeypatch.setattr(rt, 'db_cursor', db)
    monkeypatch.setattr(rt, '_pacific_today', lambda: DAY)
    monkeypatch.setattr(rt, 'BOARDS_CACHE_SECONDS', 0)
    log = {'action': 'log_board', 'board': BOARD, 'source': 'typed', 'played_on': DAY.isoformat()}
    for value in HOSTILE + NOT_TEXT:
        for body in ({'board': BOARD}, log, {'action': 'todays_boards', 'played_on': DAY.isoformat()}):
            resp = client.post('/4x6', json=dict(body, **{field: value}))
            assert resp.status_code in (200, 400), (field, value, resp.status_code)
    for sql, params in db.executed:
        # Only this module's own statements, with a placeholder per value.
        assert sql.count('%s') == len(params) and "'" not in sql and ';' not in sql, sql
        for value in params:
            assert value not in HOSTILE, (sql, value)
    for _sql, params in db.statements('INSERT INTO four_by_six_boards'):
        assert _STORED_BOARD.fullmatch(params[3]) and params[4] in rt.BOARD_SOURCES


@pytest.mark.parametrize('path', ['approve_typed_board', 'unapprove_board', 'remove_board'])
def test_hostile_admin_forms_never_reach_the_sql_text(client, monkeypatch, path):
    import routes.four_by_six as rt
    db = _ScriptedDB()
    monkeypatch.setattr(rt, 'db_cursor', db)
    auth = _admin_headers(monkeypatch)
    for board in HOSTILE + [BOARD]:
        for day in (DAY.isoformat(), "2026-11-23' OR 1=1", '٢٠٢٦-١١-٢٣', ''):
            resp = client.post('/4x6_admin/' + path, headers=auth, data={'board': board, 'played_on': day})
            assert resp.status_code == 302 and resp.headers['Location'].startswith('/4x6_admin')
            assert '<' not in resp.headers['Location'] and '\n' not in resp.headers['Location']
    for sql, params in db.executed:
        assert "'" not in sql and ';' not in sql and sql.count('%s') == len(params or ())
        for value in params or ():
            assert not isinstance(value, str) or len(value) <= 40
            # Only Remove takes a board as stored, so a damaged row can go.
            if path != 'remove_board' and value in HOSTILE:
                assert value == 'admin'


def test_a_hostile_stored_board_is_escaped_and_never_offered(client, boards, monkeypatch):
    bad = '"><script>x()</script>'
    for voter in ('a', 'b', 'c'):
        boards.rows[(bad, voter, DAY)] = (bad, 'typed', voter, DAY, boards.tick(), voter)
    assert _todays(client) == []
    assert '<script>x()' not in client.get('/4x6').get_data(as_text=True)
    html = client.get('/4x6_admin', headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert '<script>x()' not in html and 'has no solution' in html
    html = client.get('/4x6_admin/board', headers=_admin_headers(monkeypatch),
                      query_string={'board': bad, 'day': bad}).get_data(as_text=True)
    assert '<script>x()' not in html and 'const PRELOAD = null' in html


@pytest.mark.parametrize('raw', [b'', b'null', b'[]', b'{', b'\xff\xfe', b'[' * 60000,
                                 b'{"a":' * 10000 + b'1' + b'}' * 10000, b'{"board":' + b'9' * 5000 + b'}'],
                         ids=['empty', 'null', 'list', 'cut', 'bytes', 'deep list', 'deep object', 'huge number'])
def test_a_body_that_is_not_a_json_object_is_a_400(client, raw):
    assert client.post('/4x6', data=raw, content_type='application/json').status_code == 400


def test_the_slowest_boards_stay_cheap():
    # The most work a request can ask for: far under the cap that stops it.
    rng = random.Random(9)
    for _ in range(60):
        tiles = list(fbs.COLORS * 4)
        rng.shuffle(tiles)
        position = fbs.parse_board(''.join(tiles))
        seen = []
        fbs._search(position, lambda p: seen.append(p) or p, fbs.at_least, lambda p: False)
        assert len(set(seen)) < fbs.POSITION_CAP / 2
