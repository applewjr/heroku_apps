"""Circuits' today's boards and /circuits_admin (routes/circuits.py).

Nothing here reaches a real database: secret_pass points at the production
JawsDB. Most tests swap the module's table functions for an in-memory
stand-in (MemoryBoards); the SQL itself is checked against test_blossom's
scripted fake. Each simulated player is a different X-Forwarded-For, which
is what helpers.client_ip reads. The boards are made up, never a real
puzzle's, and the word data is test_circuits' tiny table.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from functions import circuits as cz
from functions.phrase_solver import blocklist_hash
from test_blossom import _admin_headers, _ScriptedDB
from test_circuits import PAIRS, ZIPF

DAY = date(2026, 11, 20)
B = cz.BOX
# SNOW [BALL] ROOM across the top, [BALL] [GAME] down the middle, BATTLE
# [PLAN] at the bottom: every tile and box on an arrow.
CELLS = ['SNOW', B, 'ROOM', '', B, '', 'BATTLE', B, ''] + [''] * 6
ARROWS = [[0, 1], [1, 2], [1, 4], [4, 7], [6, 7]]
ANSWERS = ['BALL', 'GAME', 'PLAN']


class MemoryBoards:
    """Stands in for circuits_boards and circuits_pairs: the writers write
    here and the readers read back, rows in routes.circuits._COLUMNS order."""

    def __init__(self):
        self.rows = {}
        self.pairs = {}
        self.clock = datetime(2026, 11, 20, 6, 0)
        self.reads = 0

    def tick(self):
        self.clock += timedelta(minutes=1)
        return self.clock

    def store(self, day, voter, prefix, key, cells, arrows, source, entered, top, version, shape='5x3'):
        import routes.circuits as rc
        others = {k for (k, v, d) in self.rows if v == voter and d == day and k != key}
        if len(others) >= rc.BOARDS_PER_PLAYER_PER_DAY:
            return
        self.rows[(key, voter, day)] = [key, cells, rc._arrows_text(arrows), '', None, entered, top,
                                        source, voter, day, self.tick(), prefix, shape]

    def approve(self, day, site, key, cells, arrows, answers=None, shape='5x3'):
        import routes.circuits as rc
        voter = f'admin:{site}'
        old = self.rows.get((key, voter, day))
        kept = answers if answers is not None else (old[rc.ANSWERS] if old else None)
        self.rows[(key, voter, day)] = [key, cells, rc._arrows_text(arrows), site, kept, None, None,
                                        'admin', voter, day, self.tick(), None, shape]

    def save_answers(self, key, answers):
        hit = [row for row in self.rows.values() if row[0] == key and row[7] == 'admin']
        for row in hit:
            row[4] = answers
        return len(hit)

    def unapprove(self, key, site):
        gone = [k for k, row in self.rows.items() if row[0] == key and row[8] == f'admin:{site}']
        for k in gone:
            del self.rows[k]
        return len(gone)

    def remove(self, key, day):
        gone = [k for k, row in self.rows.items() if row[0] == key and row[9] == day]
        for k in gone:
            del self.rows[k]
        return len(gone)

    def with_key(self, key):
        self.reads += 1
        return [tuple(row) for row in self.rows.values() if row[0] == key]

    def logged_between(self, first, last):
        self.reads += 1
        keys = {row[0] for row in self.rows.values() if first <= row[9] <= last}
        return [tuple(row) for row in self.rows.values() if row[0] in keys]

    def fetch(self, sql, params):
        assert 'answers IS NOT NULL' in sql, sql
        return [(row[0], row[4], row[10]) for row in self.rows.values()
                if row[7] == 'admin' and row[4] is not None]

    def added(self):
        return sorted(((a, b, t) for (a, b), t in self.pairs.items()), key=lambda r: r[2], reverse=True)

    def add_pair(self, first, second):
        self.pairs.setdefault((first, second), self.tick())

    def drop_pair(self, first, second):
        return 1 if self.pairs.pop((first, second), None) else 0

    def mine(self, source=None):
        return [row for row in self.rows.values() if row[7] != 'admin' and (source is None or row[7] == source)]


@pytest.fixture
def tiny(monkeypatch):
    import routes.circuits as rc
    data = cz.Data(**cz.pack(PAIRS, zipf=ZIPF))
    monkeypatch.setattr(rc, '_static', {'data': data, 'failed_at': 0.0, 'version': None})
    return data


@pytest.fixture
def boards(monkeypatch, tiny):
    """The in-memory tables, today's boards live, and the game day fixed."""
    import routes.circuits as rc
    memory = MemoryBoards()
    monkeypatch.setattr(rc, 'store_board', memory.store)
    monkeypatch.setattr(rc, 'store_approval', memory.approve)
    monkeypatch.setattr(rc, 'save_answers', memory.save_answers)
    monkeypatch.setattr(rc, 'remove_approval', memory.unapprove)
    monkeypatch.setattr(rc, 'remove_board', memory.remove)
    monkeypatch.setattr(rc, 'rows_with_key', memory.with_key)
    monkeypatch.setattr(rc, 'rows_logged_between', memory.logged_between)
    monkeypatch.setattr(rc, '_fetch', memory.fetch)
    monkeypatch.setattr(rc, 'added_pairs', memory.added)
    monkeypatch.setattr(rc, 'store_pair', memory.add_pair)
    monkeypatch.setattr(rc, 'delete_pair', memory.drop_pair)
    monkeypatch.setattr(rc, '_game_today', lambda: DAY)
    monkeypatch.setattr(rc, 'CIRCUITS_BOARDS_LIVE', True)
    # Every read is fresh: the memo has tests of its own.
    monkeypatch.setattr(rc, 'BOARDS_CACHE_SECONDS', 0)
    return memory


def _player(n):
    return {'X-Forwarded-For': f'198.51.100.{n}' if isinstance(n, int) else n}


def _log(client, player, cells=CELLS, arrows=ARROWS, source='typed', entered=None):
    body = {'action': 'log_board', 'cells': cells, 'arrows': arrows, 'source': source}
    if entered is not None:
        body['entered'] = entered
    resp = client.post('/circuits', headers=_player(player), json=body)
    assert resp.status_code == 200 and resp.get_json() == {'status': 'success'}


def _todays(client, player=99):
    return client.post('/circuits', headers=_player(player), json={'action': 'todays_boards'}).get_json()['boards']


def _solve(client, cells=CELLS, arrows=ARROWS, **extra):
    return client.post('/circuits', headers=_player(98), json=dict(cells=cells, arrows=arrows, **extra)).get_json()


def _served(arrows=ARROWS, cells=CELLS, sites=(), rows=5, cols=3):
    return {'cells': cells, 'rows': rows, 'cols': cols, 'arrows': sorted(arrows), 'sites': list(sites)}


# --- a day of players ------------------------------------------------------------

def test_two_identical_boards_serve_at_once(client, boards):
    _log(client, 1)
    assert _todays(client) == []
    _log(client, 2)
    assert _todays(client) == [_served()]


def test_three_boards_serve_each_arrow_by_majority(client, boards):
    _log(client, 1)
    # Player 2 missed an arrow between the boxes, and still has a finished board.
    _log(client, 2, arrows=[a for a in ARROWS if a != [1, 4]])
    assert _todays(client) == []
    _log(client, 3)
    assert _todays(client) == [_served()]


def test_an_unfinished_or_listed_board_is_never_logged(client, boards, monkeypatch):
    import routes.circuits as rc
    # ROOM has no arrow: still being typed.
    _log(client, 1, arrows=[a for a in ARROWS if a != [1, 2]])
    assert boards.mine() == []
    # A word on the offensive-word list, on a tile.
    monkeypatch.setitem(rc._blocklist, 'value', frozenset({blocklist_hash('battle')}))
    _log(client, 2)
    assert boards.mine() == []


def test_a_loaded_board_keeps_words_but_never_counts(client, boards):
    _log(client, 1)
    # Loaded and left as it is: nothing to log without a kept word...
    _log(client, 2, source='loaded')
    assert len(boards.mine()) == 1
    # ...and with one, the words are kept for James but it isn't a board.
    _log(client, 2, source='loaded', entered={'1': 'ball'})
    assert [row[5] for row in boards.mine('loaded')] == ['BALL||']
    assert _todays(client) == []


def test_a_board_only_gains_arrows(client, boards):
    _log(client, 1)
    _log(client, 1, arrows=[a for a in ARROWS if a != [1, 4]])
    [row] = boards.mine()
    assert row[2] == '0-1,1-2,1-4,4-7,6-7'


def test_an_edited_board_that_only_lost_arrows_is_not_logged(client, boards):
    _log(client, 1)
    _log(client, 2)
    _log(client, 3, arrows=[a for a in ARROWS if a != [1, 4]], source='edited')
    assert len(boards.mine()) == 2


def test_players_on_one_network_count_once(client, boards):
    _log(client, '2001:db8:1:1::5')
    _log(client, '2001:db8:1:2::9')       # the same /48
    assert _todays(client) == []
    _log(client, 7)
    assert _todays(client) == [_served()]


def test_one_player_logs_at_most_ten_boards_a_day(client, boards):
    import routes.circuits as rc
    for n in range(rc.BOARDS_PER_PLAYER_PER_DAY + 2):
        cells = list(CELLS)
        cells[2] = 'ROOM' if n == 0 else f"ROOM{'X' * n}"
        _log(client, 1, cells=cells)
    assert len(boards.mine()) == rc.BOARDS_PER_PLAYER_PER_DAY


def test_typed_cells_of_an_offered_board_get_its_arrows(client, boards):
    _log(client, 1)
    _log(client, 2)
    # Other cells: not that board, whatever its arrows.
    cells = list(CELLS)
    cells[3] = 'FOOT'
    assert _solve(client, cells=cells, arrows=ARROWS + [[3, 4]])['known_arrows'] is None
    # Its cells with an arrow missing: the page gets the board's arrows.
    reply = _solve(client, arrows=[a for a in ARROWS if a != [1, 4]])
    assert reply['known_arrows'] == sorted(ARROWS)
    assert _solve(client)['known_arrows'] is None


def test_the_dark_launch_logs_but_offers_nothing(client, boards, monkeypatch):
    import routes.circuits as rc
    monkeypatch.setattr(rc, 'CIRCUITS_BOARDS_LIVE', False)
    _log(client, 1)
    _log(client, 2)
    assert len(boards.mine()) == 2
    assert _todays(client) == []
    assert _solve(client, arrows=[a for a in ARROWS if a != [1, 4]])['known_arrows'] is None
    auth = _admin_headers(monkeypatch)
    reply = client.post('/circuits_admin/board', headers=auth, json={'action': 'todays_boards'}).get_json()
    assert reply['boards'] == [_served()]


# --- the game day ----------------------------------------------------------------

@pytest.mark.parametrize('utc, day', [
    (datetime(2026, 11, 20, 5, 30, tzinfo=timezone.utc), date(2026, 11, 19)),   # 23:30 in Chicago
    (datetime(2026, 11, 20, 6, 30, tzinfo=timezone.utc), date(2026, 11, 20)),   # 00:30
    (datetime(2026, 7, 4, 4, 30, tzinfo=timezone.utc), date(2026, 7, 3)),       # summer time: 23:30
    (datetime(2026, 7, 4, 5, 30, tzinfo=timezone.utc), date(2026, 7, 4)),
])
def test_the_game_day_is_chicagos(monkeypatch, utc, day):
    import routes.circuits as rc

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc.astimezone(tz)

    monkeypatch.setattr(rc, 'datetime', Frozen)
    assert rc._game_today() == day


def test_a_board_counts_for_the_server_day_whatever_the_players_clock(client, boards):
    _log(client, 1)
    [row] = boards.mine()
    assert row[9] == DAY


def test_todays_boards_are_read_once_a_minute(client, boards, monkeypatch):
    import routes.circuits as rc
    monkeypatch.setattr(rc, 'BOARDS_CACHE_SECONDS', 60)
    _todays(client)
    reads = boards.reads
    _todays(client)
    _todays(client)
    assert boards.reads == reads
    # An admin change is seen at once.
    auth = _admin_headers(monkeypatch)
    _approve_typed(client, auth)
    assert _todays(client) == [_served(sites=['Puzzmo'])]


# --- approvals and answers --------------------------------------------------------

def _approve_typed(client, auth, site='puzzmo', answers=None, cells=CELLS, arrows=ARROWS, day=DAY, shape=None):
    import json
    import routes.circuits as rc
    data = {'cells': json.dumps(cells), 'arrows': rc._arrows_text(arrows), 'played_on': day.isoformat(),
            'site': site, 'answers': json.dumps(answers or []), 'tab': 'boards'}
    if shape is not None:
        data['shape'] = shape
    return client.post('/circuits_admin/approve_typed_board', headers=auth, data=data)


def test_admin_pages_need_the_password(client, boards):
    for path in ('/circuits_admin', '/circuits_admin/board'):
        assert client.get(path).status_code == 401
    for path in ('/circuits_admin/approve_board', '/circuits_admin/approve_typed_board',
                 '/circuits_admin/save_answers', '/circuits_admin/unapprove_board',
                 '/circuits_admin/remove_board', '/circuits_admin/add_pair', '/circuits_admin/remove_pair'):
        assert client.post(path).status_code == 401, path


def test_an_approval_serves_first_for_its_site_with_its_answers(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    # Two players settle another board first.
    other = list(CELLS)
    other[0] = 'FOOT'
    _log(client, 1, cells=other)
    _log(client, 2, cells=other)
    resp = _approve_typed(client, auth, answers=ANSWERS)
    assert resp.status_code == 302 and 'success=' in resp.headers['Location']
    assert _todays(client) == [_served(sites=['Puzzmo']), _served(cells=other)]
    # A solve of that board shows James's answers as checked...
    reply = _solve(client)
    assert reply['checked'] is True
    assert [(b['word'], b['confidence']) for b in reply['boxes']] == [(w, 'checked') for w in ANSWERS]
    # ...but never over a word the player keeps.
    reply = _solve(client, locked={'4': 'show'})
    assert [b['word'] for b in reply['boxes']] == ['BALL', 'SHOW', 'PLAN']
    # Yahoo's approval of the same board is its own.
    _approve_typed(client, auth, site='yahoo')
    assert _todays(client)[0]['sites'] == ['Puzzmo', 'Yahoo']
    client.post('/circuits_admin/unapprove_board', headers=auth, data={'key': _key(), 'site': 'puzzmo'})
    assert _todays(client)[0]['sites'] == ['Yahoo']


def _key(cells=CELLS):
    import routes.circuits as rc
    return rc.board_key(cells)


# A 4 by 4 board (Yahoo's shape so far): SNOW [BALL] ROOM on its second row,
# [GAME] under BALL, PLAN under that.
CELLS_4 = [''] * 4 + ['SNOW', B, 'ROOM', ''] + ['', B, '', ''] + ['', 'PLAN', '', '']
ARROWS_4 = [[4, 5], [5, 6], [5, 9], [9, 13]]


def test_a_four_by_four_board_is_logged_served_and_approved_in_its_shape(client, boards, monkeypatch):
    import routes.circuits as rc
    shape = {'rows': 4, 'cols': 4}
    for p in (1, 2):
        resp = client.post('/circuits', headers=_player(p),
                           json=dict(action='log_board', cells=CELLS_4, arrows=ARROWS_4, source='typed', **shape))
        assert resp.get_json() == {'status': 'success'}
    assert {row[rc.SHAPE] for row in boards.mine()} == {'4x4'}
    assert _todays(client) == [_served(arrows=ARROWS_4, cells=CELLS_4, rows=4, cols=4)]
    # Its key is its shape and cells: the same cells can't be another shape's.
    assert next(iter(boards.rows))[0] == rc.board_key(CELLS_4, 4, 4) != rc.board_key(CELLS_4)
    # A player typing those cells gets the arrows the others drew.
    reply = client.post('/circuits', headers=_player(9), json=dict(cells=CELLS_4, arrows=ARROWS_4[:3], **shape)).get_json()
    assert reply['known_arrows'] == ARROWS_4
    # James approves one he typed, with answers: it's served in its shape,
    # the answers show as checked, and the admin page draws it 4 wide.
    auth = _admin_headers(monkeypatch)
    other = list(CELLS_4)
    other[4] = 'FOOT'
    resp = _approve_typed(client, auth, cells=other, arrows=ARROWS_4, answers=['BALL', 'GAME'], shape='4x4')
    assert 'success=' in resp.headers['Location']
    assert _todays(client)[0] == _served(arrows=ARROWS_4, cells=other, rows=4, cols=4, sites=['Puzzmo'])
    reply = client.post('/circuits', headers=_player(9), json=dict(cells=other, arrows=ARROWS_4, **shape)).get_json()
    assert [(b['word'], b['confidence']) for b in reply['boxes']] == [('BALL', 'checked'), ('GAME', 'checked')]
    html = client.get('/circuits_admin', headers=auth).get_data(as_text=True)
    assert 'mini-board c4' in html and 'mini-board c3' not in html
    assert client.get('/circuits_admin/board?key=' + rc.board_key(other, 4, 4), headers=auth).status_code == 200
    # A shape the page doesn't offer isn't approved.
    resp = _approve_typed(client, auth, cells=other, arrows=ARROWS_4, shape='6x6')
    assert 'error=' in resp.headers['Location']


def test_an_unfinished_or_bad_board_is_not_approved(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    resp = _approve_typed(client, auth, arrows=[a for a in ARROWS if a != [1, 2]])
    assert 'error=' in resp.headers['Location']
    resp = _approve_typed(client, auth, site='elsewhere')
    assert 'error=' in resp.headers['Location']
    resp = _approve_typed(client, auth, answers=['BALL', 'two words', 'PLAN'])
    assert 'error=' in resp.headers['Location']
    assert not [row for row in boards.rows.values() if row[7] == 'admin']


def test_add_approves_the_latest_player_board_only_if_nothing_newer_came_in(client, boards, monkeypatch):
    import routes.circuits as rc
    auth = _admin_headers(monkeypatch)
    _log(client, 1)
    [row] = boards.mine()
    stamp = row[rc.UPDATED].strftime(rc._STAMP)
    form = {'key': _key(), 'played_on': DAY.isoformat(), 'site': 'puzzmo', 'latest': stamp, 'tab': 'boards'}
    _log(client, 2)
    resp = client.post('/circuits_admin/approve_board', headers=auth, data=form)
    assert 'newer' in resp.headers['Location']
    newest = max(boards.mine(), key=lambda r: r[rc.UPDATED])
    form['latest'] = newest[rc.UPDATED].strftime(rc._STAMP)
    resp = client.post('/circuits_admin/approve_board', headers=auth, data=form)
    assert 'success=' in resp.headers['Location']
    assert _todays(client) == [_served(sites=['Puzzmo'])]


def test_answers_go_with_an_approval(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1)
    form = {'key': _key(), 'answer': ANSWERS, 'tab': 'boards'}
    resp = client.post('/circuits_admin/save_answers', headers=auth, data=form)
    assert 'Approve' in resp.headers['Location']
    _approve_typed(client, auth)
    resp = client.post('/circuits_admin/save_answers', headers=auth, data=form)
    assert 'success=' in resp.headers['Location']
    assert _solve(client)['checked'] is True
    resp = client.post('/circuits_admin/save_answers', headers=auth, data=dict(form, answer=['BALL', 'GAME']))
    assert 'error=' in resp.headers['Location']


def test_remove_deletes_that_days_entries(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1)
    _approve_typed(client, auth)
    resp = client.post('/circuits_admin/remove_board', headers=auth,
                       data={'key': _key(), 'played_on': DAY.isoformat(), 'tab': 'boards'})
    assert 'Removed+2+entries' in resp.headers['Location']
    assert boards.rows == {}


# --- the admin page ---------------------------------------------------------------

def test_the_boards_tab_shows_each_board_with_its_answers_and_missing_pairs(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1, entered={'1': 'ball'})
    _log(client, 2, entered={'1': 'ball'})
    html = client.get('/circuits_admin', headers=auth).get_data(as_text=True)
    assert 'Served' in html and 'Add for Puzzmo' in html and 'Add for Yahoo' in html
    assert f'/circuits_admin/board?key={_key()}' in html
    # James's real answers: GAME is right, but say the middle box was SHOW.
    _approve_typed(client, auth, answers=['BALL', 'SHOW', 'PLAN'])
    html = client.get('/circuits_admin', headers=auth).get_data(as_text=True)
    assert 'Puzzmo approved' in html and 'value="SHOW"' in html
    assert 'box 2 (SHOW, it says GAME)' in html
    # BALL + SHOW and SHOW + PLAN aren't in the tiny data: each can be added.
    assert 'Add BALL + SHOW' in html and 'Add SHOW + PLAN' in html


def test_the_answer_boxes_fill_with_what_players_kept(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1, entered={'4': 'show'})
    _log(client, 2, entered={'4': 'show'})
    _approve_typed(client, auth)
    html = client.get('/circuits_admin', headers=auth).get_data(as_text=True)
    # Box 1 from the solver, box 2 from the two players who kept SHOW.
    assert 'value="BALL"' in html and 'value="SHOW"' in html


def test_added_pairs_teach_the_solver(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    cells = ['SNOW', B, ''] + [''] * 12
    assert _solve(client, cells=cells, arrows=[[0, 1]])['boxes'][0]['word'] != 'ROOM'
    resp = client.post('/circuits_admin/add_pair', headers=auth, data={'first': 'snow', 'second': 'room', 'tab': 'pairs'})
    assert 'success=' in resp.headers['Location']
    # An added pair is as sure as any: SNOW ROOM now beats SNOW BALL.
    assert _solve(client, cells=cells, arrows=[[0, 1]])['boxes'][0]['word'] == 'ROOM'
    html = client.get('/circuits_admin?tab=pairs', headers=auth).get_data(as_text=True)
    assert 'SNOW + ROOM' in html
    # A pair the data knows neither side of can't be used, so it isn't added.
    resp = client.post('/circuits_admin/add_pair', headers=auth, data={'first': 'qwerty', 'second': 'zzyzx'})
    assert 'error=' in resp.headers['Location'] and ('qwerty', 'zzyzx') not in boards.pairs
    resp = client.post('/circuits_admin/remove_pair', headers=auth, data={'first': 'snow', 'second': 'room'})
    assert 'success=' in resp.headers['Location'] and boards.pairs == {}


def test_a_two_word_tile_is_added_whole_never_as_its_last_word(client, boards, monkeypatch):
    # SHOW AND [BALL] ROOM: the tiny data has neither SHOW AND nor AND, so
    # the Boards tab offers SHOW AND + BALL whole (AND + BALL would join every
    # tile that ends in AND), and Add stores it so.
    auth = _admin_headers(monkeypatch)
    cells = ['SHOW AND'] + CELLS[1:]
    _approve_typed(client, auth, cells=cells, answers=ANSWERS)
    html = client.get('/circuits_admin', headers=auth).get_data(as_text=True)
    assert 'Add SHOW AND + BALL' in html and 'Add AND + BALL' not in html and 'value="show and"' in html
    resp = client.post('/circuits_admin/add_pair', headers=auth, data={'first': 'show and', 'second': 'ball', 'tab': 'boards'})
    assert 'success=' in resp.headers['Location'] and ('show and', 'ball') in boards.pairs
    assert 'Add SHOW AND + BALL' not in client.get('/circuits_admin', headers=auth).get_data(as_text=True)
    # The solver now knows that tile by it, on any board.
    reply = _solve(client, cells=['SHOW AND', B, ''] + [''] * 12, arrows=[[0, 1]])
    assert reply['boxes'][0]['word'] == 'BALL' and reply['unknown'] == []
    html = client.get('/circuits_admin?tab=pairs', headers=auth).get_data(as_text=True)
    assert 'SHOW AND + BALL' in html


def test_the_accuracy_tab_scores_the_data_alone(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1)
    _approve_typed(client, auth, answers=['BALL', 'SHOW', 'PLAN'])
    # An added pair from this very board doesn't flatter the score.
    client.post('/circuits_admin/add_pair', headers=auth, data={'first': 'ball', 'second': 'show'})
    html = client.get('/circuits_admin?tab=accuracy', headers=auth).get_data(as_text=True)
    assert 'Data alone:</strong> 2 / 3 boxes right' in html
    assert 'box 2: SHOW (said GAME' in html
    # What player 1 was shown when they logged it: BALL GAME PLAN.
    assert '<strong>Shown:</strong> 2 / 3' in html


def test_the_board_entry_page_solves_but_never_logs(client, boards, monkeypatch):
    auth = _admin_headers(monkeypatch)
    _log(client, 1)
    html = client.get(f'/circuits_admin/board?key={_key()}&day=2026-11-19', headers=auth).get_data(as_text=True)
    assert 'Approve for Puzzmo' in html and 'Approve for Yahoo' in html and 'value="2026-11-19"' in html
    assert '"note": "the latest player' in html
    reply = client.post('/circuits_admin/board', headers=auth, json={'cells': CELLS, 'arrows': ARROWS}).get_json()
    assert [b['word'] for b in reply['boxes']] == ANSWERS
    resp = client.post('/circuits_admin/board', headers=auth,
                       json={'action': 'log_board', 'cells': CELLS, 'arrows': ARROWS, 'source': 'typed'})
    assert resp.status_code == 400
    assert len(boards.mine()) == 1
    html = client.get('/circuits_admin/board?key=nope', headers=auth).get_data(as_text=True)
    assert "Not a board" in html


# --- the SQL itself ---------------------------------------------------------------

def test_the_writes_say_what_they_mean(monkeypatch):
    import routes.circuits as rc
    db = _ScriptedDB(answers={'SELECT COUNT(*)': (0,)})
    monkeypatch.setattr(rc, 'db_cursor', db)
    rc.store_board(DAY, 'v' * 32, 'p' * 32, _key(), '|'.join(CELLS), [(0, 1)], 'typed', None, 'BALL||', 'abcd1234',
                   shape='4x4')
    [(sql, params)] = db.statements('INSERT INTO circuits_boards')
    assert 'AS new ON DUPLICATE KEY UPDATE arrows = new.arrows' in sql
    assert params[0] == DAY and params[4] == '4x4' and params[6] == '0-1'
    assert sql.count('%s') == len(params)
    rc.store_approval(DAY, 'yahoo', _key(), '|'.join(CELLS), [(0, 1)], shape='4x4')
    sql, params = db.statements('INSERT INTO circuits_boards')[-1]
    assert 'COALESCE(new.answers, circuits_boards.answers)' in sql and params[1] == 'admin:yahoo'
    assert params[3] == '4x4' and sql.count('%s') == len(params)
    assert rc.remove_approval(_key(), 'yahoo') == 1
    assert db.statements('DELETE FROM circuits_boards WHERE board_key = %s AND voter_hash = %s')
    rc.store_pair('snow', 'room')
    assert db.statements('INSERT INTO circuits_pairs')


def test_a_player_over_the_daily_limit_logs_nothing(monkeypatch):
    import routes.circuits as rc
    db = _ScriptedDB(answers={'SELECT COUNT(*)': (rc.BOARDS_PER_PLAYER_PER_DAY,)})
    monkeypatch.setattr(rc, 'db_cursor', db)
    rc.store_board(DAY, 'v' * 32, None, _key(), '|'.join(CELLS), [(0, 1)], 'typed', None, None, None)
    assert not db.statements('INSERT INTO circuits_boards')


def test_a_failed_log_or_read_alerts_and_the_page_carries_on(client, tiny, monkeypatch, capsys):
    import routes.circuits as rc
    from monitoring import alerts
    alerts.reset_throttle()
    monkeypatch.setattr(rc, '_game_today', lambda: DAY)
    _log(client, 1)
    assert _todays(client) == []
    assert _solve(client)['boxes'][0]['word'] == 'BALL'
    out = capsys.readouterr().out
    assert 'check=circuits_board_log_failed' in out and 'check=circuits_board_read_failed' in out
