"""Typeshift's today's boards and /typeshift_admin (routes/typeshift.py).

Nothing here reaches a real database: secret_pass points at the production
JawsDB. Most tests swap the module's table functions for an in-memory
stand-in (MemoryBoards); the SQL itself is checked against test_blossom's
scripted fake. Each simulated player is a different X-Forwarded-For, which
is what helpers.client_ip reads. The boards are made up from dictionary
words, never a real puzzle's.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from test_blossom import _admin_headers, _ScriptedDB

DAY = date(2026, 11, 20)
# STONE, CRANE and BLIMP stacked, as typed top to bottom.
COLUMNS = ['SCB', 'TRL', 'OAI', 'NM', 'EP']
# The same board with each column's letters in another order.
SHUFFLED = ['BSC', 'LRT', 'IAO', 'MN', 'PE']
# Another board: CHAIR, SHORE and THINK.
OTHER = ['CST', 'H', 'AOI', 'IRN', 'REK']


def _chicago(day=DAY, hour=10, minute=0):
    import routes.typeshift as rt
    return rt.GAME_TZ.localize(datetime(day.year, day.month, day.day, hour, minute))


class MemoryBoards:
    """Stands in for typeshift_boards: the writers write here and the readers
    read back, rows in routes.typeshift._COLUMNS order."""

    def __init__(self):
        self.rows = {}
        self.clock = datetime(2026, 11, 20, 6, 0)
        self.reads = 0

    def tick(self):
        self.clock += timedelta(minutes=1)
        return self.clock

    def store(self, day, voter, prefix, key, letters, source):
        import routes.typeshift as rt
        others = {k for (k, v, d) in self.rows if v == voter and d == day and k != key}
        if len(others) >= rt.BOARDS_PER_PLAYER_PER_DAY:
            return
        self.rows[(key, voter, day)] = [key, letters, '', source, voter, day, self.tick(), prefix]

    def approve(self, day, site, key, letters):
        voter = f'admin:{site}'
        replaced = [k for k in self.rows if k[1] == voter and k[2] == day and k[0] != key]
        for k in replaced:
            del self.rows[k]
        self.rows[(key, voter, day)] = [key, letters, site, 'admin', voter, day, self.tick(), None]
        return len(replaced)

    def unapprove(self, key, site):
        gone = [k for k, row in self.rows.items() if row[0] == key and row[4] == f'admin:{site}']
        for k in gone:
            del self.rows[k]
        return len(gone)

    def remove(self, key, day):
        gone = [k for k, row in self.rows.items() if row[0] == key and row[5] == day]
        for k in gone:
            del self.rows[k]
        return len(gone)

    def with_key(self, key):
        self.reads += 1
        return [tuple(row) for row in self.rows.values() if row[0] == key]

    def logged_between(self, first, last):
        self.reads += 1
        keys = {row[0] for row in self.rows.values() if first <= row[5] <= last}
        return [tuple(row) for row in self.rows.values() if row[0] in keys]

    def mine(self, day=None):
        return [row for row in self.rows.values() if row[3] != 'admin' and (day is None or row[5] == day)]


@pytest.fixture
def clock(monkeypatch):
    """Puzzmo's clock, set by the test: 10am in Chicago on DAY to start."""
    import routes.typeshift as rt
    now = {'at': _chicago()}
    monkeypatch.setattr(rt, '_game_now', lambda: now['at'])
    return now


@pytest.fixture
def boards(monkeypatch, clock):
    """The in-memory table, today's boards live, and the clock fixed."""
    import crowd
    import routes.typeshift as rt
    memory = MemoryBoards()
    monkeypatch.setattr(rt, 'store_board', memory.store)
    monkeypatch.setattr(rt, 'store_approval', memory.approve)
    monkeypatch.setattr(rt, 'remove_approval', memory.unapprove)
    monkeypatch.setattr(rt, 'remove_board', memory.remove)
    monkeypatch.setattr(rt, 'rows_with_key', memory.with_key)
    monkeypatch.setattr(rt, 'rows_logged_between', memory.logged_between)
    monkeypatch.setattr(rt, 'TYPESHIFT_BOARDS_LIVE', True)
    # Every read is fresh: the memo has a test of its own.
    monkeypatch.setattr(rt, 'BOARDS_CACHE_SECONDS', 0)
    # The admin page's word tabs read the crowd tables: an empty scripted DB.
    monkeypatch.setattr(crowd, 'db_cursor', _ScriptedDB())
    return memory


def _player(n):
    return {'X-Forwarded-For': f'198.51.100.{n}' if isinstance(n, int) else n}


def _log(client, player, columns=COLUMNS, source='typed', day=DAY, path='/typeshift'):
    body = {'action': 'log_board', 'columns': columns, 'source': source}
    if day is not None:
        body['day'] = day.isoformat() if isinstance(day, date) else day
    resp = client.post(path, headers=_player(player), json=body)
    assert resp.status_code == 200 and resp.get_json() == {'status': 'success'}


def _reply(client, player=99):
    return client.post('/typeshift', headers=_player(player), json={'action': 'todays_boards'}).get_json()


def _todays(client):
    return _reply(client)['boards']


def _served(columns=COLUMNS, sites=()):
    return {'columns': columns, 'sites': list(sites)}


def _key(columns=COLUMNS):
    import routes.typeshift as rt
    return rt.board_key([c.lower() for c in columns])


# --- a day of players ------------------------------------------------------------

def test_two_players_with_the_same_board_serve_it(client, boards):
    _log(client, 1)
    assert _todays(client) == []
    _log(client, 2)
    assert _todays(client) == [_served()]


def test_a_column_is_its_letters_in_any_order(client, boards):
    _log(client, 1)
    _log(client, 2, columns=SHUFFLED)
    # The same board, served in the order more of its players typed (on a
    # tie, the first typed).
    assert _todays(client) == [_served()]
    _log(client, 3, columns=SHUFFLED)
    assert _todays(client) == [_served(SHUFFLED)]


def test_players_on_one_network_count_once(client, boards):
    _log(client, '2001:db8:1:1::5')
    _log(client, '2001:db8:1:2::9')       # the same /48
    assert _todays(client) == []
    _log(client, 7)
    assert _todays(client) == [_served()]


def test_a_loaded_board_is_never_logged(client, boards):
    resp = client.post('/typeshift', headers=_player(1),
                       json={'action': 'log_board', 'columns': COLUMNS, 'source': 'shared'})
    assert resp.status_code == 400
    assert boards.mine() == []


def test_an_edited_board_back_to_the_offered_one_does_not_vote(client, boards):
    _log(client, 1)
    _log(client, 2)
    _log(client, 3, columns=SHUFFLED, source='edited')
    assert len(boards.mine()) == 2
    # Edited into another board: that one is the player's own.
    _log(client, 3, columns=OTHER, source='edited')
    assert len(boards.mine()) == 3


def test_one_player_logs_at_most_ten_boards_a_day(client, boards):
    import routes.typeshift as rt
    for n in range(rt.BOARDS_PER_PLAYER_PER_DAY + 2):
        _log(client, 1, columns=COLUMNS[:4] + ['EP' + 'ABCDFGHIJKLM'[n]])
    assert len(boards.mine()) == rt.BOARDS_PER_PLAYER_PER_DAY


def test_a_relog_replaces_the_players_row(client, boards):
    _log(client, 1)
    _log(client, 1, columns=SHUFFLED)
    [row] = boards.mine()
    assert row[1] == '|'.join(SHUFFLED)


def test_the_dark_launch_logs_but_offers_nothing(client, boards, monkeypatch):
    import routes.typeshift as rt
    monkeypatch.setattr(rt, 'TYPESHIFT_BOARDS_LIVE', False)
    _log(client, 1)
    _log(client, 2)
    assert len(boards.mine()) == 2
    assert _todays(client) == []
    auth = _admin_headers(monkeypatch)
    reply = client.post('/typeshift_admin/board', headers=auth, json={'action': 'todays_boards'}).get_json()
    assert reply['boards'] == [_served()]


def test_at_most_three_boards_a_day_approvals_first(client, boards, monkeypatch):
    others = [OTHER, ['CS', 'HT', 'AOI', 'IRN', 'REK'], ['C', 'H', 'AOI', 'IRN', 'REK']]
    player = 10
    for players, columns in zip((4, 3, 2), others):
        for _ in range(players):
            player += 1
            _log(client, player, columns=columns)
    assert [b['columns'] for b in _todays(client)] == others
    _approve_typed(client, _admin_headers(monkeypatch))
    # The approval goes first, and the board with the fewest players drops off.
    assert _todays(client) == [_served(sites=['Puzzmo']), _served(others[0]), _served(others[1])]


def test_a_half_typed_board_is_not_served(client, boards):
    for n in (1, 2):
        _log(client, n, columns=COLUMNS[:4])     # the first four columns only
    assert _todays(client) == [_served(COLUMNS[:4])]
    for n in (3, 4):
        _log(client, n)
    assert _todays(client) == [_served()]


# --- the game day ----------------------------------------------------------------

@pytest.mark.parametrize('utc, day', [
    (datetime(2026, 11, 20, 5, 30, tzinfo=timezone.utc), date(2026, 11, 19)),   # 23:30 in Chicago
    (datetime(2026, 11, 20, 6, 30, tzinfo=timezone.utc), date(2026, 11, 20)),   # 00:30
    (datetime(2026, 7, 4, 4, 30, tzinfo=timezone.utc), date(2026, 7, 3)),       # summer time: 23:30
    (datetime(2026, 7, 4, 5, 30, tzinfo=timezone.utc), date(2026, 7, 4)),
])
def test_the_game_day_is_chicagos(monkeypatch, utc, day):
    import routes.typeshift as rt

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc.astimezone(tz)

    monkeypatch.setattr(rt, 'datetime', Frozen)
    assert rt._game_today() == day


@pytest.mark.parametrize('sent, stored', [
    (DAY, DAY),
    (DAY - timedelta(days=1), DAY - timedelta(days=1)),   # started before midnight Central
    (DAY - timedelta(days=2), DAY),                       # never older than yesterday
    (DAY + timedelta(days=1), DAY),                       # nor ahead of Puzzmo
    ('2026-13-01', DAY), ('soon', DAY), (None, DAY), (20261120, DAY),
])
def test_a_board_counts_for_the_day_it_was_started_within_a_day(client, boards, sent, stored):
    _log(client, 1, day=sent)
    [row] = boards.mine()
    assert row[5] == stored


def test_a_past_puzzle_is_not_served_again(client, boards, clock, monkeypatch):
    yesterday = DAY - timedelta(days=1)
    _log(client, 1, day=yesterday)
    _log(client, 2, day=yesterday)
    # Two archive players type it today: still yesterday's puzzle.
    _log(client, 3)
    _log(client, 4)
    assert _todays(client) == []
    # Unless James approves it for today.
    _approve_typed(client, _admin_headers(monkeypatch))
    assert _todays(client) == [_served(sites=['Puzzmo'])]


def test_one_player_alone_cannot_date_a_board(client, boards):
    _log(client, 1, day=DAY - timedelta(days=1))
    _log(client, 2)
    _log(client, 3)
    assert _todays(client) == [_served()]


def test_yesterdays_boards_are_offered_for_three_hours(client, boards, clock):
    yesterday = DAY - timedelta(days=1)
    _log(client, 1, columns=OTHER, day=yesterday)
    _log(client, 2, columns=OTHER, day=yesterday)
    _log(client, 3)
    _log(client, 4)
    clock['at'] = _chicago(hour=1, minute=30)
    reply = _reply(client)
    assert reply['boards'] == [_served()] and reply['yesterday'] == [_served(OTHER)]
    assert reply['day'] == DAY.isoformat() and reply['yesterday_day'] == yesterday.isoformat()
    clock['at'] = _chicago(hour=3, minute=0)
    assert _reply(client)['yesterday'] == []


def test_yesterdays_board_is_not_offered_twice(client, boards, clock, monkeypatch):
    yesterday = DAY - timedelta(days=1)
    _log(client, 1, day=yesterday)
    _log(client, 2, day=yesterday)
    _approve_typed(client, _admin_headers(monkeypatch))      # the same board, today
    clock['at'] = _chicago(hour=0, minute=10)
    reply = _reply(client)
    assert reply['boards'] == [_served(sites=['Puzzmo'])] and reply['yesterday'] == []


def test_todays_boards_are_read_once_a_minute(client, boards, monkeypatch):
    import routes.typeshift as rt
    monkeypatch.setattr(rt, 'BOARDS_CACHE_SECONDS', 60)
    _todays(client)
    reads = boards.reads
    _todays(client)
    _todays(client)
    assert boards.reads == reads
    # An admin change is seen at once.
    _approve_typed(client, _admin_headers(monkeypatch))
    assert _todays(client) == [_served(sites=['Puzzmo'])]


# --- approvals -------------------------------------------------------------------

def _approve_typed(client, auth, columns=COLUMNS, site='puzzmo', day=DAY):
    data = {'columns': json.dumps(columns), 'played_on': day.isoformat(), 'site': site, 'tab': 'boards'}
    return client.post('/typeshift_admin/approve_typed_board', headers=auth, data=data)


def test_admin_pages_need_the_password(client, boards):
    for path in ('/typeshift_admin', '/typeshift_admin/board'):
        assert client.get(path).status_code == 401
    for path in ('/typeshift_admin/approve_board', '/typeshift_admin/approve_typed_board',
                 '/typeshift_admin/unapprove_board', '/typeshift_admin/remove_board',
                 '/typeshift_admin/add_word', '/typeshift_admin/remove_word'):
        assert client.post(path).status_code == 401, path


def test_an_approval_serves_first_as_typed(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1, columns=OTHER)
    _log(client, 2, columns=OTHER)
    resp = _approve_typed(client, auth, columns=SHUFFLED)
    assert resp.status_code == 302 and 'success=' in resp.headers['Location']
    # Players typed it the other way round: the approval's order is served.
    _log(client, 3)
    _log(client, 4)
    assert _todays(client) == [_served(SHUFFLED, sites=['Puzzmo']), _served(OTHER)]


def test_one_approval_a_site_a_day(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _approve_typed(client, auth)
    resp = _approve_typed(client, auth, columns=OTHER)
    assert 'in+place+of+your+earlier' in resp.headers['Location']
    assert _todays(client) == [_served(OTHER, sites=['Puzzmo'])]


@pytest.mark.parametrize('data', [
    {'columns': 'not json', 'played_on': '2026-11-20', 'site': 'puzzmo'},
    {'columns': json.dumps(['AB']), 'played_on': '2026-11-20', 'site': 'puzzmo'},
    {'columns': json.dumps(COLUMNS), 'played_on': 'someday', 'site': 'puzzmo'},
    {'columns': json.dumps(COLUMNS), 'played_on': '2026-11-20', 'site': 'yahoo'},
])
def test_a_bad_typed_approval_changes_nothing(client, boards, monkeypatch, data):
    resp = client.post('/typeshift_admin/approve_typed_board', headers=_admin_headers(monkeypatch), data=data)
    assert resp.status_code == 302 and 'error=' in resp.headers['Location']
    assert boards.rows == {}


def _token(row):
    """What the tab's Add sends back for a row: its time and its letters."""
    return f"{row[6]:%Y-%m-%d %H:%M:%S}|{row[1]}"


def _approvals(boards):
    return [row for row in boards.rows.values() if row[3] == 'admin']


def test_add_approves_the_latest_player_board_and_checks_its_time(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1)
    [row] = boards.mine()
    form = {'key': _key(), 'played_on': DAY.isoformat(), 'site': 'puzzmo', 'latest': _token(row)}
    # A newer entry came in since the page loaded: nothing is approved.
    _log(client, 2, columns=SHUFFLED)
    resp = client.post('/typeshift_admin/approve_board', headers=auth, data=form)
    assert 'error=' in resp.headers['Location'] and not _approvals(boards)
    newest = max(boards.mine(), key=lambda r: r[6])
    form['latest'] = _token(newest)
    # Another order of the board in that same second: not the entry shown.
    shown = newest[1]
    newest[1] = '|'.join(COLUMNS)
    resp = client.post('/typeshift_admin/approve_board', headers=auth, data=form)
    assert 'error=' in resp.headers['Location'] and not _approvals(boards)
    newest[1] = shown
    resp = client.post('/typeshift_admin/approve_board', headers=auth, data=form)
    assert 'success=' in resp.headers['Location']
    assert _todays(client) == [_served(SHUFFLED, sites=['Puzzmo'])]
    for bad in ({'key': 'x'}, {'site': 'yahoo'}, {'played_on': 'never'}):
        resp = client.post('/typeshift_admin/approve_board', headers=auth, data=dict(form, **bad))
        assert 'error=' in resp.headers['Location']
    # A day with no player entry of the board: nothing to copy.
    resp = client.post('/typeshift_admin/approve_board', headers=auth, data=dict(form, played_on='2026-11-01'))
    assert 'error=' in resp.headers['Location']


def test_add_on_an_earlier_day_copies_that_days_entry(client, boards, monkeypatch):
    yesterday = DAY - timedelta(days=1)
    _log(client, 1, columns=SHUFFLED, day=yesterday)
    [old] = boards.mine(yesterday)
    _log(client, 2)                          # the same board today, typed the other way, newer
    form = {'key': _key(), 'played_on': yesterday.isoformat(), 'site': 'puzzmo', 'latest': _token(old)}
    resp = client.post('/typeshift_admin/approve_board', headers=_admin_headers(monkeypatch), data=form)
    assert 'success=' in resp.headers['Location']
    [approval] = _approvals(boards)
    assert approval[5] == yesterday and approval[1] == '|'.join(SHUFFLED)


def test_the_editor_opens_the_board_as_it_was_on_the_day_clicked(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    yesterday = DAY - timedelta(days=1)
    _log(client, 1, columns=SHUFFLED, day=yesterday)
    _log(client, 2)                          # today, the other order
    html = client.get(f'/typeshift_admin/board?key={_key()}&day={yesterday.isoformat()}',
                      headers=auth).get_data(as_text=True)
    assert '"columns": ["BSC", "LRT", "IAO", "MN", "PE"]' in html and f'value="{yesterday.isoformat()}"' in html
    html = client.get(f'/typeshift_admin/board?key={_key()}&day={DAY.isoformat()}', headers=auth).get_data(as_text=True)
    assert '"columns": ["SCB", "TRL", "OAI", "NM", "EP"]' in html


def test_the_boards_memo_is_read_and_pushed_under_its_lock(boards, monkeypatch):
    """So an admin change can't land between the look and the push, and
    have the entry it cleared put back."""
    import routes.typeshift as rt

    class Watched(dict):
        def get(self, key, default=None):
            assert rt._boards_lock.locked(), 'read outside the lock'
            return super().get(key, default)

        def __setitem__(self, key, value):
            assert rt._boards_lock.locked(), 'written outside the lock'
            super().__setitem__(key, value)

    watched = Watched()
    monkeypatch.setattr(rt, '_boards_cache', watched)
    rt._boards_for(DAY)                      # a miss, then the store
    rt._boards_for(DAY)                      # stale at once: pushed out, read again
    assert DAY in watched


def test_undo_and_remove(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1)
    _approve_typed(client, auth)
    resp = client.post('/typeshift_admin/unapprove_board', headers=auth, data={'key': _key(), 'site': 'puzzmo'})
    assert 'success=' in resp.headers['Location'] and _todays(client) == []
    resp = client.post('/typeshift_admin/unapprove_board', headers=auth, data={'key': _key(), 'site': 'puzzmo'})
    assert 'error=' in resp.headers['Location']
    _log(client, 2)
    assert _todays(client) == [_served()]
    resp = client.post('/typeshift_admin/remove_board', headers=auth,
                       data={'key': _key(), 'played_on': DAY.isoformat()})
    assert 'success=' in resp.headers['Location'] and boards.rows == {} and _todays(client) == []


def test_the_admin_page_shows_each_day_and_why(client, boards, monkeypatch):
    _log(client, 1)
    _log(client, 2)
    _log(client, 3, columns=OTHER)
    html = client.get('/typeshift_admin', headers=_admin_headers(monkeypatch)).get_data(as_text=True)
    assert 'Game day (America/Chicago):</strong> 2026-11-20' in html
    assert 'Served' in html and 'needs 1 more player' in html
    assert f'/typeshift_admin/board?key={_key()}&amp;day=2026-11-20' in html
    assert '<span>S</span><span>C</span><span>B</span>' in html


def test_the_board_entry_page_opens_a_board_and_never_logs(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1, columns=SHUFFLED)
    html = client.get(f'/typeshift_admin/board?key={_key()}&day=2026-11-20', headers=auth).get_data(as_text=True)
    assert '"columns": ["BSC", "LRT", "IAO", "MN", "PE"]' in html and "the latest player" in html
    assert 'value="2026-11-20"' in html and 'Approve for Puzzmo' in html
    assert 'Frequently Asked Questions' not in html and 'More daily puzzle solvers' not in html
    for body in ({'action': 'log_board', 'columns': COLUMNS, 'source': 'typed'},
                 {'action': 'reject', 'columns': COLUMNS, 'word': 'stone'}):
        assert client.post('/typeshift_admin/board', headers=auth, json=body).status_code == 400
    assert len(boards.mine()) == 1
    assert client.post('/typeshift_admin/board', headers=auth, json={'columns': COLUMNS}).status_code == 200
    html = client.get('/typeshift_admin/board?key=nope', headers=auth).get_data(as_text=True)
    assert 'Not a board' in html
    html = client.get(f"/typeshift_admin/board?key={'0' * 40}", headers=auth).get_data(as_text=True)
    assert 'No board has that key' in html


# --- the SQL itself ---------------------------------------------------------------

def test_the_writes_say_what_they_mean(monkeypatch):
    import routes.typeshift as rt
    db = _ScriptedDB(answers={'SELECT COUNT(*)': (0,)})
    monkeypatch.setattr(rt, 'db_cursor', db)
    rt.store_board(DAY, 'v' * 32, 'p' * 32, _key(), '|'.join(COLUMNS), 'typed')
    [(sql, params)] = db.statements('INSERT INTO typeshift_boards')
    assert 'AS new ON DUPLICATE KEY UPDATE letters = new.letters' in sql
    assert params[0] == DAY and params[4] == 'SCB|TRL|OAI|NM|EP' and sql.count('%s') == len(params)
    rt.store_approval(DAY, 'puzzmo', _key(), '|'.join(COLUMNS))
    sql, params = db.statements('INSERT INTO typeshift_boards')[-1]
    assert params[1] == 'admin:puzzmo' and params[5] == 'admin' and sql.count('%s') == len(params)
    steps = [sql.split(' ')[0] for sql, _ in db.executed[-3:]]
    assert steps == ['START', 'DELETE', 'INSERT']
    assert db.executed[-2][1] == ('admin:puzzmo', DAY, _key())
    failing = _ScriptedDB(fail='INSERT INTO typeshift_boards')
    monkeypatch.setattr(rt, 'db_cursor', failing)
    with pytest.raises(RuntimeError):
        rt.store_approval(DAY, 'puzzmo', _key(), '|'.join(COLUMNS))
    assert failing.executed[-1][0] == 'ROLLBACK' and failing.commits == 0
    monkeypatch.setattr(rt, 'db_cursor', db)
    assert rt.remove_approval(_key(), 'puzzmo') == 1
    assert db.statements('DELETE FROM typeshift_boards WHERE board_key = %s AND voter_hash = %s')
    assert rt.remove_board(_key(), DAY) == 1
    rt.rows_logged_between(DAY, DAY)
    assert db.statements('WHERE board_key IN (SELECT board_key FROM typeshift_boards WHERE played_on BETWEEN')


def test_a_player_over_the_daily_limit_logs_nothing(monkeypatch):
    import routes.typeshift as rt
    db = _ScriptedDB(answers={'SELECT COUNT(*)': (rt.BOARDS_PER_PLAYER_PER_DAY,)})
    monkeypatch.setattr(rt, 'db_cursor', db)
    rt.store_board(DAY, 'v' * 32, None, _key(), '|'.join(COLUMNS), 'typed')
    assert not db.statements('INSERT INTO typeshift_boards')


def test_a_failed_log_or_read_alerts_and_the_page_carries_on(client, clock, capsys):
    from monitoring import alerts
    alerts.reset_throttle()
    _log(client, 1)
    assert _todays(client) == []
    assert client.post('/typeshift', json={'columns': COLUMNS}).get_json()['plan']
    out = capsys.readouterr().out
    assert 'check=typeshift_board_log_failed' in out and 'check=typeshift_board_read_failed' in out
